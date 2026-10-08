[繁體中文](README.md) | English

# Steam Family library exporter

Export your Steam Family library and your own playtime records to CSV and JSON. Requires Python 3.12+. You do not need to install the Steam Client.

## Getting started

1. Sign in to the [Steam Store](https://store.steampowered.com/) in your browser. Make sure you are signed in to your own account.
2. In the same browser, open the [Steam token page](https://store.steampowered.com/pointssummary/ajaxgetasyncconfig) and copy the value of `data.webapi_token`.
3. Run these commands from the project directory with `uv` installed:

```sh
uv sync
uv run python -m steam_family_export
```

Paste the token when prompted. Your input is hidden, and the tool automatically determines your SteamID.

**Paste your token only into your own terminal. Never share it in chat, Git, or an issue.** The tool does not read browser cookies, Keychain entries, or passwords, and it does not save your token.

## Output

After a successful export, the following files are created in the current directory:

- `steam_family_library.csv`: UTF-8 CSV with support for Chinese, Japanese, and other Unicode game names.
- `steam_family_library.json`: Game data and playtime sources.
- `data/raw/`: API responses from each run, with credential fields redacted.

Exported data includes family members' SteamIDs, so share it carefully. If a playtime record is missing, its value is left blank or `null` rather than reported as zero hours.

If your token expires or the token page does not return a token, sign in to the Steam Store again, reload the token page, and rerun the tool with the new token.

## More information

- [Advanced usage and data details](docs/advanced-usage.md) (Chinese): Environment variables, CLI options, playtime attribution, troubleshooting, and tests.
- [API research notes](docs/api-research.md) (Chinese): Endpoints, authentication, schemas, and verification status.

This tool uses unofficial APIs, and complete playtime history is not guaranteed. Library eligibility reflects ownership or family sharing; it does not guarantee that a copy is currently available or that a game supports macOS. See the advanced documentation for details.
