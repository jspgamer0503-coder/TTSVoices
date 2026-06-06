# Contributing to TTS Voices

Thanks for your interest in improving TTS Voices! This document
covers how to report bugs, suggest features, and submit code.

## Reporting bugs

1. Run the app and click **🐞 Bug Log → Save to file**.
2. Include the exported log in your issue.
3. Note the version number shown in the header (e.g. `v2.5.0`).
4. Describe the steps to reproduce, what you expected, and what
   actually happened.

## Suggesting features

Open a GitHub Issue with the **enhancement** label. Explain the
use case, not just the solution — "I want to be able to X because Y"
is more useful than "add button for Z".

## Submitting code

1. **Fork** the repository and create a topic branch:
   ```bash
   git checkout -b feature/my-change
   ```
2. Keep changes focused. One feature or fix per PR.
3. Match the existing code style (4-space indent, type hints
   where the surrounding code has them, docstrings for public
   functions).
4. Test on at least one of: Ubuntu 24.04, Kali, Debian 12, Linux
   Mint 21.
5. Update `CHANGELOG.md` under the "Unreleased" section.
6. Open a Pull Request and describe what changed and why.

## Development setup

```bash
git clone https://github.com/<you>/TTSVoices
cd TTSVoices
./install.sh        # creates ./venv and installs dependencies
source venv/bin/activate
python3 ttsvoices.py
```

## Code of conduct

Be respectful. This is a hobby project maintained by one person
with AI assistance — please be patient with response times.
