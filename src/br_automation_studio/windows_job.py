
"""Run local vendor commands and their children in owned Windows Job Objects."""

from contextlib import contextmanager
from dataclasses import dataclass
import ctypes
from ctypes import wintypes
import json
import subprocess
import sys
import time

# The worker waits for its command until the parent has assigned it to the job.
# The native program therefore inherits containment from the start.
WORKER = """
import json
import subprocess
import sys

args = json.load(sys.stdin)
startup = subprocess.STARTUPINFO()
startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
startup.wShowWindow = subprocess.SW_HIDE
raise SystemExit(subprocess.call(args, stdin=subprocess.DEVNULL,
                                creationflags=subprocess.CREATE_NO_WINDOW, startupinfo=startup))
"""


class BasicLimits(ctypes.Structure):
    _fields_ = [
        ("process_time", ctypes.c_int64), ("job_time", ctypes.c_int64), ("flags", wintypes.DWORD),
        ("min_working_set", ctypes.c_size_t), ("max_working_set", ctypes.c_size_t),
        ("active_process_limit", wintypes.DWORD), ("affinity", ctypes.c_size_t),
        ("priority_class", wintypes.DWORD), ("scheduling_class", wintypes.DWORD),
    ]


class ExtendedLimits(ctypes.Structure):
    _fields_ = [
        ("basic", BasicLimits), ("io", ctypes.c_uint64 * 6),
        ("process_memory", ctypes.c_size_t), ("job_memory", ctypes.c_size_t),
        ("peak_process_memory", ctypes.c_size_t), ("peak_job_memory", ctypes.c_size_t),
    ]


class Accounting(ctypes.Structure):
    _fields_ = [
        ("times", ctypes.c_int64 * 4), ("page_faults", wintypes.DWORD),
        ("total_processes", wintypes.DWORD), ("active_processes", wintypes.DWORD),
        ("terminated_processes", wintypes.DWORD),
    ]


def win_function(dll, name, result, *argtypes):
    function = getattr(dll, name)
    function.restype = result
    function.argtypes = argtypes
    return function


class Job:
    def __enter__(self):
        if sys.platform != "win32":
            raise OSError("Native Automation Studio builds require Windows")

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        create = win_function(kernel, "CreateJobObjectW", wintypes.HANDLE, wintypes.LPVOID, wintypes.LPCWSTR)
        self.close = win_function(kernel, "CloseHandle", wintypes.BOOL, wintypes.HANDLE)
        self.open_process = win_function(kernel, "OpenProcess", wintypes.HANDLE,
                                         wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        self.assign = win_function(kernel, "AssignProcessToJobObject", wintypes.BOOL,
                                   wintypes.HANDLE, wintypes.HANDLE)
        self.terminate = win_function(kernel, "TerminateJobObject", wintypes.BOOL,
                                      wintypes.HANDLE, wintypes.UINT)
        self.query = win_function(kernel, "QueryInformationJobObject", wintypes.BOOL,
                                  wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD, wintypes.LPVOID)
        configure = win_function(kernel, "SetInformationJobObject", wintypes.BOOL,
                                 wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD)

        self.handle = create(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = ExtendedLimits()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not configure(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            error = ctypes.WinError(ctypes.get_last_error())
            self.close(self.handle)
            raise error
        return self

    def __exit__(self, *exc):
        self.close(self.handle)

    def add(self, pid):
        process = self.open_process(0x0101, False, pid)  # PROCESS_SET_QUOTA | PROCESS_TERMINATE
        if not process:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            if not self.assign(self.handle, process):
                raise ctypes.WinError(ctypes.get_last_error())
        finally:
            self.close(process)

    def active(self):
        info = Accounting()
        if not self.query(self.handle, 1, ctypes.byref(info), ctypes.sizeof(info), None):
            raise ctypes.WinError(ctypes.get_last_error())
        return info.active_processes

    def pids(self):
        count_size = 2 * ctypes.sizeof(wintypes.DWORD)
        pid_size = ctypes.sizeof(ctypes.c_size_t)
        for slots in (32, 256, 2048):
            data = ctypes.create_string_buffer(count_size + slots * pid_size)
            if not self.query(self.handle, 3, data, ctypes.sizeof(data), None):
                error = ctypes.get_last_error()
                if error in (122, 234):
                    continue
                raise ctypes.WinError(error)
            assigned, returned = (wintypes.DWORD * 2).from_buffer(data)
            if returned <= slots and assigned == returned:
                return set((ctypes.c_size_t * returned).from_buffer(data, count_size))
        raise OSError("Could not read the complete Windows job PID list")

    def stop(self):
        if self.active() and not self.terminate(self.handle, 1):
            raise ctypes.WinError(ctypes.get_last_error())

    def drain(self):
        deadline = time.monotonic() + 5
        while self.active():
            if time.monotonic() >= deadline:
                if not self.terminate(self.handle, 1):
                    raise ctypes.WinError(ctypes.get_last_error())
                for _ in range(100):
                    if not self.active():
                        return "terminated"
                    time.sleep(0.05)
                return "unverified"
            time.sleep(0.05)
        return "empty"


def waiting_worker(working_directory, log):
    return subprocess.Popen([sys.executable, "-I", "-S", "-c", WORKER],
                            cwd=working_directory, stdin=subprocess.PIPE, stdout=log,
                            stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NO_WINDOW)


@dataclass
class ObservedProcess:
    job: Job
    worker: subprocess.Popen
    cleanup: str | None = None


@contextmanager
def observed_process(arguments, working_directory, log):
    with Job() as job:
        with waiting_worker(working_directory, log) as worker:
            try:
                job.add(worker.pid)
            except BaseException:
                worker.kill()
                raise
            session = ObservedProcess(job, worker)
            try:
                worker.stdin.write(json.dumps(arguments).encode("ascii"))
                worker.stdin.close()
                yield session
            finally:
                try:
                    job.stop()
                    worker.wait(timeout=10)
                    session.cleanup = job.drain()
                except BaseException:
                    if worker.poll() is None:
                        worker.kill()
                    raise


def run_build(arguments, working_directory, begin_dispatch, log, timeout):
    with Job() as job:
        with waiting_worker(working_directory, log) as worker:
            try:
                job.add(worker.pid)
                begin_dispatch(worker.pid)
            except BaseException:
                worker.kill()
                raise
            timed_out = False
            try:
                worker.communicate(json.dumps(arguments).encode("ascii"), timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
                if not job.terminate(job.handle, 1):
                    worker.kill()
                    raise ctypes.WinError(ctypes.get_last_error())
                try:
                    worker.wait(timeout=10)
                except subprocess.TimeoutExpired as error:
                    worker.kill()
                    raise OSError("The terminated worker did not exit within the expected time") from error
            except BaseException:
                if not job.terminate(job.handle, 1):
                    worker.kill()
                raise
            cleanup = job.drain()
            return worker.returncode, timed_out, cleanup
