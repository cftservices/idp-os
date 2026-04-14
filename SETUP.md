# Windows Task Scheduler Setup

Run this in PowerShell as Administrator to register the daily 10:00 task:

```powershell
$action = New-ScheduledTaskAction -Execute "c:\tools\linkedin-intel\run_daily.bat"
$trigger = New-ScheduledTaskTrigger -Daily -At "10:00AM"
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -RunOnlyIfNetworkAvailable:$false
Register-ScheduledTask -TaskName "LinkedIn Intel Daily" -Action $action -Trigger $trigger -Settings $settings -RunLevel Highest
```

`-StartWhenAvailable` ensures the task runs even if the laptop was off at 10:00.

## One-Time LinkedIn Login

Run this ONCE to log in to LinkedIn with the persistent browser profile:

```bash
cd c:/tools/linkedin-intel/scraper
python -c "
from playwright.sync_api import sync_playwright
import os
from pathlib import Path
from dotenv import load_dotenv
load_dotenv()
profile = os.getenv('CHROMIUM_PROFILE', 'c:/tools/linkedin-intel/browser-profile')
with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(user_data_dir=profile, headless=False)
    page = ctx.new_page()
    page.goto('https://www.linkedin.com/login')
    input('Log in manually in the browser, then press ENTER here...')
    ctx.close()
print('Profile saved. Future runs will reuse this session.')
"
```

## Manual Test Run

```bash
cd c:/tools/linkedin-intel/scraper
python run.py
```

Then in Claude Code: `/linkedin-leads`
