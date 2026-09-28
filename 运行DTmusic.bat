@echo off
chcp 65001 >nul
setlocal

rem 使用批处理文件所在目录查找 Python 源程序。
cd /d "%~dp0"
if not exist "dtmusic_gui.py" goto missing_source

rem 优先使用可直接调用的 conda.exe，再检查本机现有安装位置。
set "CONDA_CMD="
for /f "delims=" %%I in ('where conda.exe 2^>nul') do if not defined CONDA_CMD set "CONDA_CMD=%%I"
if not defined CONDA_CMD if exist "D:\ProgramData\anaconda3\Scripts\conda.exe" set "CONDA_CMD=D:\ProgramData\anaconda3\Scripts\conda.exe"
if not defined CONDA_CMD goto missing_conda

"%CONDA_CMD%" run -n DTmusic python "%~dp0dtmusic_gui.py" %*
if errorlevel 1 goto launch_failed
exit /b 0

:missing_source
echo Missing dtmusic_gui.py next to this script.
pause
exit /b 1

:missing_conda
echo Conda was not found. Add conda.exe to PATH.
pause
exit /b 1

:launch_failed
echo DTmusic failed to start. See the error above.
pause
exit /b 1
