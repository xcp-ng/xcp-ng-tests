from __future__ import annotations

import logging
import time
import xml.etree.ElementTree as ET
from typing import cast, Optional, Sequence, Union

from lib.commands import ssh, SSHCommandFailed
from lib.common import wait_for
from lib.typing import AnswerfileDict, Self, SimpleAnswerfileDict

class AnswerFile:
    def __init__(self, kind: str, /) -> None:
        from data import BASE_ANSWERFILES
        defn: SimpleAnswerfileDict = BASE_ANSWERFILES[kind]
        self.defn = self._normalize_structure(defn)

    def write_xml(self, filename: str) -> None:
        etree = ET.ElementTree(self._defn_to_xml_et(self.defn))
        etree.write(filename)

    # chainable mutators for lambdas

    def top_append(self, *defs: Union[SimpleAnswerfileDict, None, ValueError]) -> Self:
        assert not isinstance(self.defn['CONTENTS'], str), "a toplevel CONTENTS must be a list"
        for defn in defs:
            if defn is None:
                continue
            self.defn['CONTENTS'].append(self._normalize_structure(defn))
        return self

    def top_setattr(self, attrs: "dict[str, str]") -> Self:
        assert 'CONTENTS' not in attrs
        self.defn.update(cast(AnswerfileDict, attrs))
        return self

    # makes a mutable deep copy of all `contents`
    @staticmethod
    def _normalize_structure(defn: Union[SimpleAnswerfileDict, ValueError]) -> AnswerfileDict:
        assert isinstance(defn, dict), f"{defn!r} is not a dict"
        assert 'TAG' in defn, f"{defn} has no TAG"

        # type mutation through nearly-shallow copy
        new_defn: AnswerfileDict = {
            'TAG': defn['TAG'],
            'CONTENTS': [],
        }
        for key, value in defn.items():
            if key == 'CONTENTS':
                if isinstance(value, str):
                    new_defn['CONTENTS'] = value
                else:
                    value_as_sequence: Sequence["SimpleAnswerfileDict"]
                    if isinstance(value, Sequence):
                        value_as_sequence = value
                    else:
                        value_as_sequence = (
                            cast(SimpleAnswerfileDict, value),
                        )
                    new_defn['CONTENTS'] = [
                        AnswerFile._normalize_structure(item)
                        for item in value_as_sequence
                        if item is not None
                    ]
            elif key == 'TAG':
                pass            # already copied
            else:
                new_defn[key] = value # type: ignore[literal-required]

        return new_defn

    # convert to a ElementTree.Element tree suitable for further
    # modification before we serialize it to XML
    @staticmethod
    def _defn_to_xml_et(defn: AnswerfileDict, /, *, parent: Optional[ET.Element] = None) -> ET.Element:
        assert isinstance(defn, dict)
        defn_copy = dict(defn)
        name = defn_copy.pop('TAG')
        assert isinstance(name, str)
        contents = cast(Union[str, "list[AnswerfileDict]"], defn_copy.pop('CONTENTS', []))
        assert isinstance(contents, (str, list))
        defn_filtered = cast("dict[str, str]", defn_copy)
        element = ET.Element(name, {}, **defn_filtered)
        if parent is not None:
            parent.append(element)
        if isinstance(contents, str):
            element.text = contents
        else:
            for content in contents:
                AnswerFile._defn_to_xml_et(content, parent=element)
        return element

def poweroff(ip: str) -> None:
    try:
        ssh(ip, ["poweroff"])
    except SSHCommandFailed as e:
        # ignore connection closed by reboot
        if e.returncode == 255 and "closed by remote host" in e.stdout:
            logging.info("sshd closed the connection")
            pass
        else:
            raise

def monitor_install(*, ip: str) -> None:
    # wait for "yum install" phase to finish
    wait_for(lambda: ssh(ip, ["grep",
                              "'DISPATCH: NEW PHASE: Completing installation'",
                              "/tmp/install-log"],
                         check=False, simple_output=False,
                         ).returncode == 0,
             "Wait for rpm installation to succeed",
             timeout_secs=40 * 60) # FIXME too big

    # wait for install to finish
    wait_for(lambda: ssh(ip, ["grep",
                              "'The installation completed successfully'",
                              "/tmp/install-log"],
                         check=False, simple_output=False,
                         ).returncode == 0,
             "Wait for system installation to succeed",
             timeout_secs=40 * 60) # FIXME too big

    wait_for(lambda: ssh(ip, ["ps a|grep '[0-9]. python /opt/xensource/installer/init'"],
                         check=False, simple_output=False,
                         ).returncode == 1,
             "Wait for installer to terminate")

def monitor_upgrade(*, ip: str) -> None:
    # wait for "yum install" phase to start
    wait_for(lambda: ssh(ip, ["grep",
                              "'DISPATCH: NEW PHASE: Reading package information'",
                              "/tmp/install-log"],
                         check=False, simple_output=False,
                         ).returncode == 0,
             "Wait for upgrade preparations to finish",
             timeout_secs=40 * 60) # FIXME too big

    # wait for "yum install" phase to finish
    wait_for(lambda: ssh(ip, ["grep",
                              "'DISPATCH: NEW PHASE: Completing installation'",
                              "/tmp/install-log"],
                         check=False, simple_output=False,
                         ).returncode == 0,
             "Wait for rpm installation to succeed",
             timeout_secs=40 * 60) # FIXME too big

    # wait for install to finish
    wait_for(lambda: ssh(ip, ["grep",
                              "'The installation completed successfully'",
                              "/tmp/install-log"],
                         check=False, simple_output=False,
                         ).returncode == 0,
             "Wait for system installation to succeed",
             timeout_secs=40 * 60) # FIXME too big

    wait_for(lambda: ssh(ip, ["ps a|grep '[0-9]. python /opt/xensource/installer/init'"],
                         check=False, simple_output=False,
                         ).returncode == 1,
             "Wait for installer to terminate")

def monitor_restore(*, ip: str) -> None:
    # wait for "yum install" phase to start
    wait_for(lambda: ssh(ip, ["grep",
                              "'Restoring backup'",
                              "/tmp/install-log"],
                         check=False, simple_output=False,
                         ).returncode == 0,
             "Wait for data restoration to start",
             timeout_secs=40 * 60) # FIXME too big

    # wait for "yum install" phase to finish
    wait_for(lambda: ssh(ip, ["grep",
                              "'Data restoration complete.  About to re-install bootloader.'",
                              "/tmp/install-log"],
                         check=False, simple_output=False,
                         ).returncode == 0,
             "Wait for data restoration to complete",
             timeout_secs=40 * 60) # FIXME too big

    # The installer will not terminate in restore mode, it
    # requires human interaction and does not even log it, so
    # wait for last known action log (tested with 8.3b2)
    wait_for(lambda: ssh(ip, ["grep",
                              "'ran .*swaplabel.*rc 0'",
                              "/tmp/install-log"],
                         check=False, simple_output=False,
                         ).returncode == 0,
             "Wait for installer to hopefully finish",
             timeout_secs=40 * 60) # FIXME too big

    # "wait a bit to be extra sure".  Yuck.
    time.sleep(30)

    logging.info("Shutting down Host VM after successful restore")
