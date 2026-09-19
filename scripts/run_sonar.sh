#!/usr/bin/env bash
# ==============================================================================
# run_sonar.sh — SonarQube Scanner runner script for Jenkins CI/CD
#
# Runs SonarQube static code analysis and uploads reports to the Sonar server.
# Reads settings from sonar-project.properties.
# ==============================================================================
set -euo pipefail

echo "============================================================"
echo "▶ Running SonarQube Analysis"
echo "============================================================"

# Check if reports exist
if [[ ! -f "reports/coverage.xml" ]]; then
  echo "⚠ Note: reports/coverage.xml not found; coverage will not be uploaded."
fi

if command -v sonar-scanner >/dev/null 2>&1; then
  echo "Using system sonar-scanner..."
  sonar-scanner -Dproject.settings=sonar-project.properties
elif [[ -n "${SONAR_SCANNER_HOME:-}" ]] && [[ -x "${SONAR_SCANNER_HOME}/bin/sonar-scanner" ]]; then
  echo "Using SONAR_SCANNER_HOME: ${SONAR_SCANNER_HOME}..."
  "${SONAR_SCANNER_HOME}/bin/sonar-scanner" -Dproject.settings=sonar-project.properties
else
  echo "ERROR: 'sonar-scanner' executable not found in PATH or SONAR_SCANNER_HOME." >&2
  echo "Configure the SonarQube Scanner tool in Jenkins before enabling RUN_SONAR." >&2
  exit 1
fi
echo "✓ SonarQube analysis finished successfully."
