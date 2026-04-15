"""
Denver Bot — Google OAuth2 setup script.
Run this once to generate token.json from credentials.json.
"""

import os
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/contacts.readonly",
]


def main():
    """Run OAuth2 flow and save token.json."""
    credentials_file = "credentials.json"

    if not os.path.exists(credentials_file):
        print(f"❌ No se encontró '{credentials_file}'.")
        print("   Descargalo desde Google Cloud Console → APIs & Services → Credentials")
        print("   y colocalo en el directorio raíz del proyecto.")
        return

    print("🔐 Iniciando flujo de autenticación OAuth2...")
    print(f"   Scopes: {', '.join(SCOPES)}")
    print()

    flow = InstalledAppFlow.from_client_secrets_file(credentials_file, SCOPES)
    creds = flow.run_local_server(port=0)

    with open("token.json", "w") as token_file:
        token_file.write(creds.to_json())

    print()
    print("✅ token.json generado exitosamente.")
    print("   El bot puede usar Google Calendar, Gmail y Contacts.")


if __name__ == "__main__":
    main()
