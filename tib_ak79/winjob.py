"""Windows Job Object limits (memory, CPU rate, process count, kill-on-close) via ctypes.  Windows only.

NOT VERIFIED ON WINDOWS (written on Linux; only structure sizes are tested).  Struct layouts follow the Win32 headers:
JOBOBJECT_BASIC_LIMIT_INFORMATION, IO_COUNTERS, JOBOBJECT_EXTENDED_LIMIT_INFORMATION, JOBOBJECT_CPU_RATE_CONTROL_INFORMATION.
Usage: job = create_job(...); assign(job, process_handle) right after the subject process is created suspended.
"""
from __future__ import annotations

import ctypes
import sys

JOB_OBJECT_LIMIT_PROCESS_MEMORY = 0x00000100
JOB_OBJECT_LIMIT_JOB_MEMORY = 0x00000200
JOB_OBJECT_LIMIT_ACTIVE_PROCESS = 0x00000008
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
JOB_OBJECT_LIMIT_JOB_TIME = 0x00000004
JobObjectExtendedLimitInformation = 9
JobObjectCpuRateControlInformation = 15
JOB_OBJECT_CPU_RATE_CONTROL_ENABLE = 0x1
JOB_OBJECT_CPU_RATE_CONTROL_HARD_CAP = 0x4


class BasicLimit(ctypes.Structure):
    _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", ctypes.c_uint32), ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", ctypes.c_uint32),
                ("Affinity", ctypes.c_size_t), ("PriorityClass", ctypes.c_uint32), ("SchedulingClass", ctypes.c_uint32)]


class IoCounters(ctypes.Structure):
    _fields_ = [(n, ctypes.c_uint64) for n in ("ReadOps", "WriteOps", "OtherOps", "ReadBytes", "WriteBytes", "OtherBytes")]


class ExtendedLimit(ctypes.Structure):
    _fields_ = [("Basic", BasicLimit), ("Io", IoCounters), ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t)]


class CpuRate(ctypes.Structure):
    _fields_ = [("ControlFlags", ctypes.c_uint32), ("CpuRate", ctypes.c_uint32)]  # CpuRate: 1..10000 = percent * 100


def extended_limit(mem_mb: int, max_processes: int) -> ExtendedLimit:
    if mem_mb <= 0 or max_processes <= 0:
        raise ValueError("limits must be positive")
    lim = ExtendedLimit()
    lim.Basic.LimitFlags = (JOB_OBJECT_LIMIT_PROCESS_MEMORY | JOB_OBJECT_LIMIT_JOB_MEMORY
                            | JOB_OBJECT_LIMIT_ACTIVE_PROCESS | JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE)
    lim.Basic.ActiveProcessLimit = max_processes
    lim.ProcessMemoryLimit = lim.JobMemoryLimit = mem_mb * 1024 * 1024
    return lim


def cpu_rate(percent: int) -> CpuRate:
    if not 1 <= percent <= 100:
        raise ValueError("percent must be 1..100")
    return CpuRate(JOB_OBJECT_CPU_RATE_CONTROL_ENABLE | JOB_OBJECT_CPU_RATE_CONTROL_HARD_CAP, percent * 100)


def create_job(mem_mb: int, max_processes: int, cpu_percent: int):
    if sys.platform != "win32":
        raise OSError("winjob is Windows-only")
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateJobObjectW.restype = ctypes.c_void_p
    job = k32.CreateJobObjectW(None, None)
    if not job:
        raise ctypes.WinError(ctypes.get_last_error())
    for cls, info in ((JobObjectExtendedLimitInformation, extended_limit(mem_mb, max_processes)),
                      (JobObjectCpuRateControlInformation, cpu_rate(cpu_percent))):
        if not k32.SetInformationJobObject(ctypes.c_void_p(job), cls, ctypes.byref(info), ctypes.sizeof(info)):
            raise ctypes.WinError(ctypes.get_last_error())
    return job


def assign(job, process_handle) -> None:
    if sys.platform != "win32":
        raise OSError("winjob is Windows-only")
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    if not k32.AssignProcessToJobObject(ctypes.c_void_p(job), ctypes.c_void_p(process_handle)):
        raise ctypes.WinError(ctypes.get_last_error())


def resume_process(process_handle) -> None:
    """Resume a process created with CREATE_SUSPENDED (all its threads), after it has been assigned to the job."""
    if sys.platform != "win32":
        raise OSError("winjob is Windows-only")
    nt = ctypes.WinDLL("ntdll")
    status = nt.NtResumeProcess(ctypes.c_void_p(process_handle))
    if status != 0:
        raise OSError(f"NtResumeProcess failed: {status:#x}")
