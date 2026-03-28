"""
Balance-check and reconciliation logic.

After a normal sync completes, compare the Akahu account balance against the
YNAB account balance.  If they differ by more than 1 cent we assume transactions
are missing and run a 30-day look-back against the live YNAB transaction list to
find and import the gaps.
"""

import logging
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from ..models.database import AkahuAccount, SyncLog
from ..schemas.transaction import TransactionCreate
from .akahu_client import AkahuClient
from .dedup import DeduplicationService
from .ynab_client import YNABClient

logger = logging.getLogger(__name__)

# Tolerance in dollars — YNAB stores milliunits so rounding can produce up to
# $0.001 of error; we use $0.01 to absorb any FX / rounding edge-cases.
BALANCE_TOLERANCE = 0.01


async def check_and_reconcile(
    session: AsyncSession,
    link: AkahuAccount,
    sync_log: SyncLog,
    reconciliation_days: int = 30,
) -> None:
    """
    Compare Akahu and YNAB balances and reconcile if they don't match.

    Mutates *sync_log* with the balance check results and commits them to the
    session.  On a mismatch, fetches the last *reconciliation_days* of
    transactions from YNAB, finds any Akahu transactions absent from YNAB, and
    imports them.
    """
    akahu = AkahuClient()
    ynab = YNABClient()

    # --- Fetch current balances ---
    try:
        akahu_account = await akahu.get_account(link.akahu_account_id)
        ynab_account = await ynab.get_account(link.ynab_budget_id, link.ynab_account_id)
    except Exception as exc:
        logger.warning(f"Balance check skipped for {link.akahu_account_id}: {exc}")
        sync_log.balance_checked = False
        await session.commit()
        return

    if (
        akahu_account is None
        or akahu_account.balance is None
        or ynab_account is None
    ):
        logger.warning(
            f"Balance check skipped for {link.akahu_account_id}: "
            "could not retrieve one or both balances"
        )
        sync_log.balance_checked = False
        await session.commit()
        return

    akahu_balance: float = akahu_account.balance
    ynab_balance: float = YNABClient.milliunits_to_dollars(ynab_account.get("balance", 0))

    sync_log.balance_checked = True
    sync_log.akahu_balance = akahu_balance
    sync_log.ynab_balance = ynab_balance
    sync_log.balance_matched = abs(akahu_balance - ynab_balance) <= BALANCE_TOLERANCE
    sync_log.reconciliation_triggered = False
    sync_log.reconciliation_imported = 0

    logger.info(
        f"Balance check for {link.akahu_account_id}: "
        f"Akahu={akahu_balance:.2f}  YNAB={ynab_balance:.2f}  "
        f"matched={sync_log.balance_matched}"
    )

    if sync_log.balance_matched:
        await session.commit()
        return

    # --- Balances differ — reconcile ---
    logger.info(
        f"Balance mismatch detected for {link.akahu_account_id} "
        f"(diff={akahu_balance - ynab_balance:+.2f}). "
        f"Running {reconciliation_days}-day reconciliation."
    )
    sync_log.reconciliation_triggered = True

    start_date = datetime.now() - timedelta(days=reconciliation_days)

    try:
        ynab_txs = await ynab.get_account_transactions(
            link.ynab_budget_id,
            link.ynab_account_id,
            since_date=start_date.date(),
        )
    except Exception as exc:
        logger.error(f"Failed to fetch YNAB transactions for reconciliation: {exc}")
        await session.commit()
        return

    # Build a fingerprint count map: "date:amount_milliunits" -> how many times
    # that exact fingerprint exists in YNAB already.
    ynab_counts: dict[str, int] = {}
    for tx in ynab_txs:
        key = f"{tx['date']}:{tx['amount']}"
        ynab_counts[key] = ynab_counts.get(key, 0) + 1

    try:
        akahu_txs = await akahu.get_account_transactions(
            link.akahu_account_id,
            start_date=start_date,
        )
    except Exception as exc:
        logger.error(f"Failed to fetch Akahu transactions for reconciliation: {exc}")
        await session.commit()
        return

    # Walk Akahu transactions and identify ones not yet covered in YNAB.
    # We track occurrences so that two identical-amount same-day transactions
    # are handled correctly.
    seen_counts: dict[str, int] = {}
    missing_ynab: list[dict] = []
    missing_creates: list[TransactionCreate] = []

    for tx in akahu_txs:
        amount_milli = YNABClient.dollars_to_milliunits(tx.amount)
        date_str = (
            tx.date.date().isoformat()
            if isinstance(tx.date, datetime)
            else tx.date.isoformat()
        )
        key = f"{date_str}:{amount_milli}"

        occurrence = seen_counts.get(key, 0) + 1
        seen_counts[key] = occurrence

        if occurrence > ynab_counts.get(key, 0):
            payee = tx.merchant or (tx.description[:50] if tx.description else None)
            missing_ynab.append(
                {"date": tx.date, "amount": tx.amount, "payee": payee, "memo": tx.description}
            )
            missing_creates.append(
                TransactionCreate(
                    date=tx.date,
                    amount=tx.amount,
                    payee=payee,
                    memo=tx.description,
                    source="akahu",
                    source_account=link.akahu_account_id,
                    source_transaction_id=tx.id,
                )
            )

    if not missing_ynab:
        logger.info(
            f"Reconciliation found no missing transactions for {link.akahu_account_id}. "
            "Balance difference may be due to pending/uncleared transactions."
        )
        await session.commit()
        return

    logger.info(
        f"Reconciliation importing {len(missing_ynab)} missing transactions "
        f"for {link.akahu_account_id}."
    )

    try:
        import_result = await ynab.import_transactions(
            link.ynab_budget_id,
            link.ynab_account_id,
            missing_ynab,
        )
    except Exception as exc:
        logger.error(f"Failed to import reconciliation transactions to YNAB: {exc}")
        await session.commit()
        return

    dedup = DeduplicationService(session)
    await dedup.upsert_imports_batch(
        missing_creates,
        link.ynab_budget_id,
        link.ynab_account_id,
        import_result.transaction_ids,
    )

    sync_log.reconciliation_imported = len(import_result.transaction_ids)
    logger.info(
        f"Reconciliation complete for {link.akahu_account_id}: "
        f"imported {sync_log.reconciliation_imported} transactions."
    )

    await session.commit()
