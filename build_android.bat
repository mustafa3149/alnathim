@echo off
REM ─────────────────────────────────────────────────────────────────────────────
REM  build_android.bat — Build the Al-Nathim NATIVE Android app (android_native/)
REM  Runs: gradlew assembleDebug  →  app\build\outputs\apk\debug\app-debug.apk
REM  Prereq: Android SDK (local.properties) — JDK 21 is auto-provisioned by
REM  Gradle via the foojay toolchain resolver (already cached under ~\.gradle\jdks).
REM ─────────────────────────────────────────────────────────────────────────────
setlocal

set "PROJECT_DIR=%~dp0android_native"

REM Point JAVA_HOME at the cached JDK 21 if present (fast daemon start).
set "JAVA_HOME=C:\Users\WARER\.gradle\jdks\eclipse_adoptium-21-amd64-windows.2"
if not exist "%JAVA_HOME%\bin\java.exe" (
    echo [WARN] Expected JDK 21 not found — falling back to the foojay toolchain resolver.
    set "JAVA_HOME="
)

cd /d "%PROJECT_DIR%"

echo ── Building native APK (debug) ──
call gradlew.bat assembleDebug %*
if errorlevel 1 (
    echo.
    echo ── BUILD FAILED ──
    endlocal
    exit /b 1
)

echo.
echo ── SUCCESS ──
echo APK: %PROJECT_DIR%\app\build\outputs\apk\debug\app-debug.apk
endlocal
exit /b 0
