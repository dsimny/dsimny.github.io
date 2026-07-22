#!/usr/bin/env python3
"""
Print a fresh board encryption key.

Run this yourself, on your own machine, and paste the output straight into
GitHub as the repository secret BOARD_ENCRYPTION_KEY. Keep a second copy
somewhere safe, such as a password manager.

If this key is lost, every board encrypted with it becomes permanently
unreadable: it can never be graded, and it can never be revealed on the
ledger. There is no recovery path. That is the point of encryption, and it
cuts both ways.

Run: python scripts/genkey.py
"""
from cryptography.fernet import Fernet

if __name__ == "__main__":
    print()
    print("Save this as the repository secret BOARD_ENCRYPTION_KEY,")
    print("and keep a backup copy somewhere safe:")
    print()
    print("   " + Fernet.generate_key().decode())
    print()
    print("Lose it and the encrypted boards can never be graded or revealed.")
    print()
