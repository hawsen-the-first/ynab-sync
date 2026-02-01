# YNAB Sync - YNAB Transaction Importer

A self-hosted application for importing bank transactions into [YNAB](https://www.ynab.com/) (You Need A Budget). Supports CSV file uploads and direct bank sync via [Akahu](https://www.akahu.nz/) for New Zealand banks.

## Features

- **CSV Import**: Upload CSV files from any bank with flexible column mapping
- **Akahu Integration**: Direct connection to NZ bank accounts via Akahu
- **Scheduled Auto-Sync**: Automatic background sync for Akahu accounts (1-24 hour intervals)
- **Duplicate Detection**: SQLite database tracks imported transactions to prevent duplicates
- **Pre-configured Bank Profiles**: Built-in mappings for ASB, ANZ, Westpac, BNZ, and Kiwibank
- **Custom Mapping Profiles**: Save your own CSV column mappings for reuse
- **Transaction Preview**: Review transactions before importing to YNAB
- **Docker Support**: Easy deployment with Docker and docker-compose

## Quick Start

### Prerequisites

- Docker and docker-compose
- YNAB account with a Personal Access Token
- (Optional) Akahu account for direct bank sync

### 1. Clone and Configure

```bash
# Clone the repository
cd yanb-sync

# Copy the example environment file
cp .env.example .env

# Edit .env with your API tokens
nano .env
```

### 2. Get Your API Tokens

#### YNAB Personal Access Token
1. Go to [YNAB Developer Settings](https://app.ynab.com/settings/developer)
2. Click "New Token"
3. Copy the token to your `.env` file

#### Akahu Tokens (Optional - for NZ banks)
1. Create an account at [Akahu](https://my.akahu.nz/)
2. Go to Developer settings
3. Create an app and get your App Token
4. Connect your bank accounts to get a User Token
5. Add both tokens to your `.env` file

### 3. Run with Docker

```bash
# Build and start the container
docker-compose up -d

# View logs
docker-compose logs -f

# Stop the container
docker-compose down
```

The application will be available at `http://localhost:8000`

## Usage

### CSV Import

1. Go to the **CSV Import** tab
2. Upload your bank's CSV export file
3. Select a pre-configured bank profile OR manually map columns
4. Select your YNAB budget and account
5. Click "Preview Transactions" to see what will be imported
6. Review the transactions (duplicates are highlighted)
7. Click "Import" to send transactions to YNAB

### Akahu Sync (NZ Banks)

1. Ensure Akahu tokens are configured in `.env`
2. Go to the **Akahu Sync** tab
3. Link each Akahu account to a YNAB account
4. Click "Sync Now" to import recent transactions

### Scheduled Auto-Sync

For Akahu-linked accounts, you can enable automatic background sync:

1. Link an Akahu account to YNAB (see above)
2. Click the **Schedule** button on the account
3. Enable auto-sync and configure:
   - **Sync Interval**: How often to sync (1, 2, 4, 6, 12, or 24 hours)
   - **Days to Look Back**: How many days of transactions to check (3, 7, 14, or 30)
4. Click "Save Schedule"

The scheduler runs in the background and will automatically import new transactions. You can monitor sync status and history in the UI.

### Import History

The **History** tab shows all previously imported transactions with statistics.

## Configuration

### Environment Variables

| Variable | Description | Required |
|----------|-------------|----------|
| `YNAB_ACCESS_TOKEN` | YNAB Personal Access Token | Yes |
| `AKAHU_APP_TOKEN` | Akahu App Token | No |
| `AKAHU_USER_TOKEN` | Akahu User Token | No |
| `SECRET_KEY` | Secret key for encryption | Recommended |
| `DATABASE_URL` | SQLite database path | No (has default) |

### CSV Date Formats

Supported date formats:
- `DD/MM/YYYY` (default, NZ format)
- `MM/DD/YYYY` (US format)
- `YYYY-MM-DD` (ISO format)
- `DD-MM-YYYY`

## Development

### Running Locally (without Docker)

```bash
# Create virtual environment
cd backend
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Run the server
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### API Documentation

When running, API docs are available at:
- Swagger UI: `http://localhost:8000/docs`
- ReDoc: `http://localhost:8000/redoc`

## Project Structure

```
yanb-sync/
├── backend/
│   ├── app/
│   │   ├── main.py           # FastAPI application
│   │   ├── config.py         # Configuration
│   │   ├── dependencies.py   # Dependency injection
│   │   ├── models/           # SQLAlchemy models
│   │   ├── schemas/          # Pydantic schemas
│   │   ├── services/         # Business logic
│   │   └── routers/          # API routes
│   ├── requirements.txt
│   └── Dockerfile
├── frontend/
│   ├── index.html
│   └── js/app.js
├── docker-compose.yml
├── .env.example
└── README.md
```

## Security Notes

- API tokens are stored in environment variables only
- The SQLite database contains transaction hashes but no sensitive data
- Akahu handles all bank authentication - no bank credentials are stored locally
- Run the Docker container with a non-root user (configured by default)

## Troubleshooting

### YNAB Connection Failed
- Verify your Personal Access Token is correct
- Check that the token hasn't expired
- Ensure you have at least one budget in YNAB

### Akahu Connection Failed
- Verify both App Token and User Token are configured
- Check that your bank connections are active in Akahu
- User tokens may need to be refreshed periodically

### CSV Parsing Errors
- Check the date format matches your bank's export
- Ensure the CSV has a header row
- Try adjusting the "Skip Rows" setting if your CSV has extra header lines

## License

MIT License - feel free to use and modify for your personal use.
