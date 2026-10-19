@echo off
rem Find-CR-Scan-Calibration: double-click to look for CR Scan's calibration files on this PC.
rem Read-only; copies what it finds into a new folder on the Desktop. UNTESTED.
rem Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
rem Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0find_cr_scan_calibration.ps1"
