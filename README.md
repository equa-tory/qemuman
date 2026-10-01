# qemuman
A lightweight GUI Frontend for QEMU written in Python

![image](https://user-images.githubusercontent.com/52355164/151694029-24e0ca80-a866-4986-b0f5-fe8cc98fb71f.png)

## Requirements
The following tools have to be installed to run this:
- QEMU (Refer to the [QEMU Download Page](https://www.qemu.org/download/) for the latest QEMU binaries)
- Python 3.8 or above

## Quick QEMU install on Windows
Open PowerShell and run:
```powershell
winget install SoftwareFreedomConservancy.QEMU
```
Then add it to `PATH` (this persists for new terminals; restart the terminal afterwards):
```powershell
[Environment]::SetEnvironmentVariable("Path", $env:Path + ";C:\Program Files\qemu", "User")
```
Verify with `qemu-system-x86_64 --version` and `qemu-img --version`.

## How to use?
- Clone this repository or download it as a `.zip` and extract it
- Double-click `setup.bat` (creates a virtualenv and installs the requirements)
- Double-click `start.bat` to run the program

Or manually: `pip install -r requirements.txt`, then `python qemuman.py`.

## Features
- Create qcow2 HDD images, set RAM (default 4 GB)
- SSH port forwarding (default host 2222 -> guest 22): `ssh -p 2222 user@localhost`
- Remembers ISO / HDD paths and shows a history dropdown
