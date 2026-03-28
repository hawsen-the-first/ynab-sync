"""
Balance-check and reconciliation logic.

After a normal sync completes, compare the Akahu account balance against the
YNAB account balance.  If they differ by more than 1 cent we run a progressive
reconciliation: each pass widens the look-back window until the balances agree
or we exhaust the retry schedule.

Retry windows (days): 30 → 60 → 90 → 180 → 365
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

# Progressive look-back windows in days.  Each pass widens the window and
# re-checks the balance before moving to the next.
RECONCILIATION_WINDOWS = [30, 60, 90, 180, 365]


async def _fetch_balance(akahu: AkahuClient, ynab: YNABClient, link: AkahuAccount):
    """Return (akahu_balance, ynab_balance) or raise."""
    akahu_account = await akahu.get_account(link.akahu_account_id)
    ynab_account = await ynab.get_account(link.ynab_budget_id, link.ynab_account_id)

    if akahu_account is None or akahu_account.balance is None or ynab_account is None:
        raise ValueError("Could not retrieve one or both account balances")

    return (
        akahu_account.balance,
        YNABClient.milliunits_to_dollars(ynab_account.get("balance", 0)),
    )


async def _reconcile_window(
    akahu: AkahuClient,
    ynab: YNABClient,
    dedup: DeduplicationService,
    link: AkahuAccount,
    days: int,
) -> int:
    """
    Fetch Akahu and YNAB transactions for the given window, find gaps, import
    them.  Returns the number of transactions imported into YNAB.
    """
    start_date = datetime.now() - timedelta(days=days)

    ynab_txs = await ynab.get_account_transactions(
        link.ynab_budget_id,
        link.ynab_account_id,
        since_date=start_date.date(),
    )

    # Build fingerprint count map: "date:amount_milliunits" -> occurrences in YNAB
    ynab_counts: dict[str, int] = {}
    for tx in ynab_txs:
        key = f"{tx['date']}:{tx['amount']}"
        ynab_counts[key] = ynab_counts.get(key, 0) + 1

    akahu_txs = await akahu.get_account_transactions(
        link.akahu_account_id,
        start_date=start_date,
    )

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
        return 0

    import_result = await ynab.import_transactions(
        link.ynab_budget_id,
        link.ynab_account_id,
        missing_ynab,
    )

    await dedup.upsert_imports_batch(
        missing_creates,
        link.ynab_budget_id,
        link.ynab_account_id,
        import_result.transaction_ids,
    )

    return len(import_result.transaction_ids)


async def check_and_reconcile(
    session: AsyncSession,
    link: AkahuAccount,
    sync_log: SyncLog,
) -> None:
    """
    Compare Akahu and YNAB balances and reconcile progressively if they don't match.

    Widens the look-back window across RECONCILIATION_WINDOWS, re-checking the
    balance after each pass and stopping as soon as they agree.

    Mutates *sync_log* with results and commits after each pass.
    """
    akahu = AkahuClient()
    ynab = YNABClient()

    # --- Fetch current balances ---
    try:
        akahu_balance, ynab_balance = await _fetch_balance(akahu, ynab, link)
    except Exception as exc:
        logger.warning(f"Balance check skipped for {link.akahu_account_id}: {exc}")
        sync_log.balance_checked = False
        await session.commit()
        return

    sync_log.balance_checked = True
    sync_log.akahu_balance = akahu_balance
    sync_log.ynab_balance = ynab_balance
    sync_log.balance_matched = abs(akahu_balance - ynab_balance) <= BALANCE_TOLERANCE
    sync_log.reconciliation_triggered = False
    sync_log.reconciliation_imported = 0
    sync_log.reconciliation_passes = 0
    sync_log.reconciliation_window_days = None

    logger.info(
        f"Balance check for {link.akahu_account_id}: "
        f"Akahu={akahu_balance:.2f}  YNAB={ynab_balance:.2f}  "
        f"matched={sync_log.balance_matched}"
    )

    if sync_log.balance_matched:
        await session.commit()
        return

    # --- Progressive reconciliation ---
    logger.info(
        f"Balance mismatch for {link.akahu_account_id} "
        f"(diff={akahu_balance - ynab_balance:+.2f}). "
        f"Starting progressive reconciliation over windows: {RECONCILIATION_WINDOWS} days."
    )
    sync_log.reconciliation_triggered = True
    dedup = DeduplicationService(session)
    total_imported = 0

    for days in RECONCILIATION_WINDOWS:
        sync_log.reconciliation_passes = (sync_log.reconciliation_passes or 0) + 1
        sync_log.reconciliation_window_days = days
        logger.info(
            f"Reconciliation pass {sync_log.reconciliation_passes} "
            f"({days} days) for {link.akahu_account_id}"
        )

        try:
            imported = await _reconcile_window(akahu, ynab, dedup, link, days)
        except Exception as exc:
            logger.error(
                f"Reconciliation pass failed for {link.akahu_account_id} "
                f"(window={days}d): {exc}"
            )
            break

        total_imported += imported
        sync_log.reconciliation_imported = total_imported
        await session.commit()

        if imported == 0:
            logger.info(
                f"No new transactions found in {days}-day window for "
                f"{link.akahu_account_id}. Checking balance..."
            )

        # Re-check balance after this pass
        try:
            _, ynab_balance_now = await _fetch_balance(akahu, ynab, link)
        except Exception as exc:
            logger.warning(f"Could not re-check balance after reconciliation pass: {exc}")
            break

        if abs(akahu_balance - ynab_balance_now) <= BALANCE_TOLERANCE:
            sync_log.balance_matched = True
            sync_log.ynab_balance = ynab_balance_now
            await session.commit()
            logger.info(
                f"Balance reconciled for {link.akahu_account_id} after "
                f"{sync_log.reconciliation_passes} pass(es) "
                f"({days}-day window). "
                f"Total imported: {total_imported}."
            )
            return

        logger.info(
            f"Balance still differs after {days}-day pass for {link.akahu_account_id} "
            f"(diff={akahu_balance - ynab_balance_now:+.2f}). "
            f"{'Widening window.' if days != RECONCILIATION_WINDOWS[-1] else 'All windows exhausted.'}"
        )

    # Exhausted all windows — record final state
    sync_log.balance_matched = False
    await session.commit()
    logger.warning(
        f"Reconciliation exhausted all windows for {link.akahu_account_id}. "
        f"Remaining diff may be pending/uncleared transactions or manual adjustments. "
        f"Total imported across all passes: {total_imported}."
    )
