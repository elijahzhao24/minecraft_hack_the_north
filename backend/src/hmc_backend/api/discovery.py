"""Zero-config Bonjour / mDNS service discovery for HMC backend.

Registers ``_hmc._tcp.local`` on the configured port using the built-in macOS
``/usr/bin/dns-sd`` utility, allowing iOS capture devices to discover the backend
automatically over local Wi-Fi or Personal Hotspot.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import threading
from typing import Optional

logger = logging.getLogger(__name__)


class BonjourAdvertiser:
    """Manages advertising the HMC backend via Bonjour / mDNS."""

    def __init__(
        self,
        name: str = "HMC-Backend",
        service_type: str = "_hmc._tcp",
        port: int = 8000,
        domain: str = "local",
    ) -> None:
        self.name = name
        self.service_type = service_type
        self.port = port
        self.domain = domain
        self._proc: Optional[subprocess.Popen] = None
        self._stopped = False

    def start(self) -> bool:
        """Start advertising the service in the background."""
        self._stopped = False
        dns_sd_path = shutil.which("dns-sd") or "/usr/bin/dns-sd"
        if not os.path.exists(dns_sd_path):
            logger.info("dns-sd not found at %s; Bonjour auto-discovery disabled", dns_sd_path)
            return False

        cmd = [
            dns_sd_path,
            "-R",
            self.name,
            self.service_type,
            self.domain,
            str(self.port),
        ]
        try:
            self._proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
            )
            logger.info(
                "Bonjour service '%s' (%s.%s) registered on port %d (PID %d)",
                self.name,
                self.service_type,
                self.domain,
                self.port,
                self._proc.pid,
            )
            return True
        except Exception as exc:
            logger.warning("Failed to start dns-sd: %s", exc)
            self._proc = None
            return False

    def stop(self) -> None:
        """Stop advertising the service and terminate the subprocess."""
        self._stopped = True
        proc = self._proc
        self._proc = None
        if proc is not None:
            try:
                proc.terminate()
                proc.wait(timeout=1.0)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
            logger.info("Bonjour service '%s' unregistered", self.name)


_advertiser: Optional[BonjourAdvertiser] = None


def start_discovery(port: int = 8000, name: str = "HMC-Backend") -> Optional[BonjourAdvertiser]:
    """Start the global Bonjour advertiser if on macOS."""
    global _advertiser
    if _advertiser is not None:
        _advertiser.stop()
    _advertiser = BonjourAdvertiser(name=name, port=port)
    _advertiser.start()
    return _advertiser


def stop_discovery() -> None:
    """Stop the global Bonjour advertiser."""
    global _advertiser
    if _advertiser is not None:
        _advertiser.stop()
        _advertiser = None
