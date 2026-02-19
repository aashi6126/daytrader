"""
Schwab OAuth2 Authorization Setup

Run this script to authenticate with Schwab for the first time,
or to re-authenticate when the refresh token expires (every 7 days).

Usage:
    cd backend
    python -m scripts.auth_setup

What happens:
    1. Opens your browser to the Schwab login page
    2. You log in and authorize the app
    3. Schwab redirects to the callback URL (page won't load - that's expected)
    4. Copy the FULL URL from the browser address bar
    5. Paste it here when prompted
    6. Tokens are saved to ~/.schwabdev/tokens.db
    7. The server will use these tokens automatically
"""
import os
import sys

# Add the backend directory to sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()


def main():
    from app.config import Settings

    settings = Settings()

    if settings.SCHWAB_APP_KEY == "change-me":
        print("ERROR: SCHWAB_APP_KEY not configured.")
        print("Copy .env.example to .env and fill in your Schwab credentials.")
        sys.exit(1)

    print("=" * 60)
    print("  Schwab OAuth2 Authorization Setup")
    print("=" * 60)
    print()
    print(f"  App Key:      {settings.SCHWAB_APP_KEY[:8]}...")
    print(f"  Callback URL: {settings.SCHWAB_CALLBACK_URL}")
    print(f"  Tokens DB:    {settings.SCHWAB_TOKENS_DB}")
    print()

    try:
        import schwabdev
    except ImportError:
        print("ERROR: schwabdev is not installed.")
        print("Run: pip install schwabdev")
        sys.exit(1)

    tokens_db = os.path.expanduser(settings.SCHWAB_TOKENS_DB)

    print("This will open your browser to log in to Schwab.")
    print("After login, Schwab redirects to the callback URL.")
    print("The page WON'T load — that's expected.")
    print("Copy the FULL URL from the address bar and paste it below.")
    print()

    # schwabdev.Client handles the full OAuth flow:
    # 1. Opens browser to auth URL
    # 2. Prompts for callback URL via input()
    # 3. Exchanges code for tokens
    # 4. Saves tokens to SQLite DB
    client = schwabdev.Client(
        settings.SCHWAB_APP_KEY,
        settings.SCHWAB_APP_SECRET,
        settings.SCHWAB_CALLBACK_URL,
        tokens_db=tokens_db,
    )

    # Verify by fetching account info
    try:
        accounts = client.linked_accounts().json()
        print()
        print("SUCCESS! Authenticated with Schwab.")
        print()
        for acc in accounts:
            print(f"  Account: {acc.get('accountNumber', 'N/A')}")
            print(f"  Hash:    {acc.get('hashValue', 'N/A')}")
            if not settings.SCHWAB_ACCOUNT_HASH:
                print()
                print(f"  Add this to your .env:")
                print(f"  SCHWAB_ACCOUNT_HASH={acc.get('hashValue', '')}")
        print()
        print(f"Tokens saved to: {tokens_db}")
        print("The server will use these tokens automatically.")
        print("Re-run this script if auth expires (every 7 days).")
    except Exception as e:
        print(f"Warning: Could not verify account: {e}")
        print("Tokens may still be saved. Try starting the server.")


if __name__ == "__main__":
    main()
