import json
import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

def _find_epubcheck():
    """Auto-detect epubcheck.jar location."""
    import shutil
    candidates = [
        os.environ.get("EPUBCHECK_JAR", ""),
        "/tmp/epubcheck-5.2.1/epubcheck.jar",
        "/usr/local/share/epubcheck/epubcheck.jar",
        "/opt/epubcheck/epubcheck.jar",
    ]
    # Also check if epubcheck is on PATH (wrapper script)
    if shutil.which("epubcheck"):
        return "epubcheck"  # system-installed
    for c in candidates:
        if c and Path(c).exists():
            return c
    return None


import os
EPUBCHECK_JAR = _find_epubcheck()


class QualityChecker:
    """Stage 6: Validate EPUB with epubcheck and ACE by DAISY."""

    def __init__(self, epubcheck_path: str = None):
        self.epubcheck_path = epubcheck_path or EPUBCHECK_JAR

    def check(self, epub_path: str) -> dict:
        epub_path = str(epub_path)
        report = {
            'epub_path': epub_path,
            'epubcheck': None,
            'ace': None,
            'passed': False,
        }

        # Run epubcheck
        ec_result = self._run_epubcheck(epub_path)
        report['epubcheck'] = ec_result

        # Run ACE (if available)
        ace_result = self._run_ace(epub_path)
        report['ace'] = ace_result

        # Determine pass/fail
        ec_pass = ec_result.get('errors', 1) == 0 if ec_result else False
        ace_pass = ace_result.get('violations', 1) == 0 if ace_result else True  # optional

        report['passed'] = ec_pass
        if ec_pass:
            logger.info("EPUB validation PASSED")
        else:
            logger.warning("EPUB validation FAILED")

        return report

    def _run_epubcheck(self, epub_path: str) -> dict:
        """Run W3C epubcheck."""
        jar = self.epubcheck_path
        if not jar:
            logger.warning("epubcheck not found. Install it or set EPUBCHECK_JAR env var.")
            return {'error': 'epubcheck not installed', 'errors': -1}

        try:
            if jar.endswith('.jar'):
                if not Path(jar).exists():
                    logger.warning(f"epubcheck not found at {jar}")
                    return {'error': 'epubcheck not installed', 'errors': -1}
                cmd = ['java', '-jar', jar, epub_path]
            else:
                cmd = [jar, epub_path]

            result = subprocess.run(
                cmd,
                capture_output=True, text=True, timeout=120
            )

            errors = 0
            warnings = 0
            messages = []

            # Parse epubcheck output lines (skip JAVA_TOOL_OPTIONS noise)
            output = result.stdout + result.stderr
            for line in output.split('\n'):
                line = line.strip()
                if not line or line.startswith('Picked up'):
                    continue
                # Match actual epubcheck error/warning lines
                if line.startswith('ERROR(') or line.startswith('FATAL('):
                    errors += 1
                    messages.append(('ERROR', line))
                elif line.startswith('WARNING('):
                    warnings += 1
                    messages.append(('WARNING', line))
                # Parse summary line
                elif 'fatals' in line and 'errors' in line:
                    import re
                    m = re.search(r'(\d+) fatals? / (\d+) errors? / (\d+) warnings?', line)
                    if m:
                        errors = int(m.group(1)) + int(m.group(2))
                        warnings = int(m.group(3))

            logger.info(f"epubcheck: {errors} errors, {warnings} warnings")
            return {
                'errors': errors,
                'warnings': warnings,
                'messages': messages[:20],
                'returncode': result.returncode,
            }

        except subprocess.TimeoutExpired:
            logger.error("epubcheck timed out")
            return {'error': 'timeout', 'errors': -1}
        except Exception as e:
            logger.error(f"epubcheck failed: {e}")
            return {'error': str(e), 'errors': -1}

    def _run_ace(self, epub_path: str) -> dict:
        """Run ACE by DAISY accessibility checker."""
        try:
            # Check if ace is available
            which = subprocess.run(['which', 'ace'], capture_output=True)
            if which.returncode != 0:
                logger.warning("ACE not found in PATH")
                return {'error': 'ace not installed', 'violations': -1}

            report_dir = Path(epub_path).parent / "ace_report"
            report_dir.mkdir(exist_ok=True)

            result = subprocess.run(
                ['ace', epub_path, '-o', str(report_dir)],
                capture_output=True, text=True, timeout=120
            )

            # Parse ACE report
            report_file = report_dir / "report.json"
            if report_file.exists():
                with open(report_file) as f:
                    ace_report = json.load(f)

                violations = 0
                for assertion in ace_report.get('assertions', []):
                    if assertion.get('earl:result', {}).get('earl:outcome') == 'fail':
                        violations += 1

                logger.info(f"ACE: {violations} violations")
                return {
                    'violations': violations,
                    'report_path': str(report_dir),
                }
            else:
                return {
                    'output': result.stdout[:500],
                    'violations': -1,
                }

        except subprocess.TimeoutExpired:
            logger.error("ACE timed out")
            return {'error': 'timeout', 'violations': -1}
        except Exception as e:
            logger.error(f"ACE failed: {e}")
            return {'error': str(e), 'violations': -1}
