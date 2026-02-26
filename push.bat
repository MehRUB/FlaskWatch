@echo off
echo.
echo === FlaskTube GitHub Push ===
echo.

git add .

set /p msg=Commit message (or press Enter for "Update"): 
if "%msg%"=="" set msg=Update

git commit -m "%msg%"
git push

echo.
if %errorlevel%==0 (
    echo SUCCESS - pushed to GitHub!
) else (
    echo FAILED - see error above.
)
echo.
pause