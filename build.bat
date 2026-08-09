@echo off
:: build.bat bll|ui  — x64 debug only
:: Requires STC_BUILD_ROOT set by "dos configure".

if "%~1"=="" (
    echo Usage: build.bat [bll^|ui]
    exit /b 1
)
if "%STC_BUILD_ROOT%"=="" (
    echo ERROR: STC_BUILD_ROOT not set. Run "dos configure testcenter x64 debug" first.
    exit /b 1
)

if /i "%~1"=="bll" (
    cd /d %STC_BUILD_ROOT%
    call .\scons.cmd -j 20 -f SConstruct.bll debug=1 target=bll-win64
    copy /y %USERPROFILE%\stcbll.ini %STC_BUILD_ROOT%\bin\Debug_x64\
    exit /b %errorlevel%
) else if /i "%~1"=="ui" (
    cd /d %STC_BUILD_ROOT%
    python genSln.py
    call "C:\Program Files (x86)\Microsoft Visual Studio\2017\Professional\Common7\Tools\VsDevCmd.bat" -arch=x64
    msbuild.exe TestCenter.UI.Gen.sln /t:build /p:Configuration=Debug /p:Platform="x64" /m:4
    exit /b %errorlevel%
) else (
    echo ERROR: unknown target "%~1". Use bll or ui.
    exit /b 1
)
