# sorter-combo-smtp

A lightweight dataset toolkit for scanning combo/smtp files, deduplicating against master lists, extracting domains, and exporting reports.

## Requirements

- Python 3.9+
- PySide6>=6.6.0
- tqdm>=4.65.0

## Install

```bash
python -m pip install -r requirements.txt
```

## Run

Use the graphical interface:

```bash
python gui.py
```

Or run the startup helper script:

```bash
./start.sh
```

For command-line mode:

```bash
python main.py
```

## Features

- GUI scanner for combo and SMTP datasets
- Keyword filtering and format checks with smart SMTP detection
- Auto-separation of fresh results by common domains
- Domain/TLD extraction from master or custom files
- CLI option to merge both combo + SMTP datasets in one run
- Open output and reports folders directly from the GUI menu

## Automation helper

A scheduling helper script is available at `scripts/auto_commit_push.sh`.

Example usage:

```bash
./scripts/auto_commit_push.sh "Auto scheduled commit"
```

To run on the current branch and push to `fork` if configured, add a cron job like:

```bash
crontab -e
# Add:
0 2 * * * cd /workspaces/sorter-combo-smtp && ./scripts/auto_commit_push.sh "Nightly auto commit" >> /tmp/auto_commit.log 2>&1
```

## Data files explained

- `data/combo/master.txt` — full deduplicated combo dataset
- `data/combo/master_keys.txt` — cached combo dedupe keys used to speed loading
- `data/smtp/smtp_master.txt` — full deduplicated SMTP dataset
- `data/smtp/smtp_keys.txt` — cached SMTP dedupe keys used to speed loading

The `master_keys` files are not the full credentials; they are just the unique dedup keys extracted from the master dataset. They make repeated runs much faster because the app can load keys without parsing the full master file.
