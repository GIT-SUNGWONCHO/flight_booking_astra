"""준비 단계의 소유 프로세스만 관리한다. 정상 종료 후 Chrome 자식은 유지한다."""
import os
import subprocess


class StageProcess:
    def __init__(self, args, **kwargs):
        self.job = None
        self.process = None
        if os.name != 'nt':
            self.process = subprocess.Popen(args, **kwargs)
            return
        import ctypes as c
        from ctypes import wintypes as w
        self.c = c
        self.k = k = c.WinDLL('kernel32', use_last_error=True)
        for name, argspec, result in [
            ('CreateJobObjectW', [c.c_void_p, w.LPCWSTR], w.HANDLE),
            ('AssignProcessToJobObject', [w.HANDLE, w.HANDLE], w.BOOL),
            ('TerminateJobObject', [w.HANDLE, w.UINT], w.BOOL),
            ('CloseHandle', [w.HANDLE], w.BOOL),
            ('CreateToolhelp32Snapshot', [w.DWORD, w.DWORD], w.HANDLE),
            ('OpenThread', [w.DWORD, w.BOOL, w.DWORD], w.HANDLE),
            ('ResumeThread', [w.HANDLE], w.DWORD),
        ]:
            fn = getattr(k, name)
            fn.argtypes, fn.restype = argspec, result
        self.job = k.CreateJobObjectW(None, None)
        if not self.job:
            raise c.WinError(c.get_last_error())
        try:
            # 子 프로세스를 만들기 전에 Job에 넣는다. 준비 외부 작업자는 포함하지 않는다.
            kwargs['creationflags'] = kwargs.get('creationflags', 0) | 0x4  # CREATE_SUSPENDED
            self.process = subprocess.Popen(args, **kwargs)
            if not k.AssignProcessToJobObject(self.job, int(self.process._handle)):
                raise c.WinError(c.get_last_error())
            self._resume_initial_thread()
        except BaseException:
            if self.process is not None:
                k.TerminateJobObject(self.job, 1)
                if self.process.poll() is None:
                    self.process.kill()  # 아직 정지된 소유 부모: Job 할당 실패 시에도 회수
                self.process.wait(timeout=2)
            self.close()
            raise

    def _resume_initial_thread(self):
        c, k = self.c, self.k
        from ctypes import wintypes as w

        class ThreadEntry(c.Structure):
            _fields_ = [('dwSize', w.DWORD), ('cntUsage', w.DWORD),
                        ('threadID', w.DWORD), ('ownerPID', w.DWORD),
                        ('basePriority', w.LONG), ('deltaPriority', w.LONG), ('flags', w.DWORD)]

        for name in ('Thread32First', 'Thread32Next'):
            fn = getattr(k, name)
            fn.argtypes, fn.restype = [w.HANDLE, c.POINTER(ThreadEntry)], w.BOOL
        snapshot = k.CreateToolhelp32Snapshot(0x4, 0)  # TH32CS_SNAPTHREAD
        if snapshot == c.c_void_p(-1).value:
            raise c.WinError(c.get_last_error())
        try:
            row = ThreadEntry()
            row.dwSize = c.sizeof(row)
            more = k.Thread32First(snapshot, c.byref(row))
            while more:
                if row.ownerPID == self.process.pid:
                    thread = k.OpenThread(0x2, False, row.threadID)  # THREAD_SUSPEND_RESUME
                    if not thread:
                        raise c.WinError(c.get_last_error())
                    try:
                        if k.ResumeThread(thread) != 1:
                            raise RuntimeError('준비 단계 초기 스레드 재개 실패')
                    finally:
                        k.CloseHandle(thread)
                    return
                row.dwSize = c.sizeof(row)
                more = k.Thread32Next(snapshot, c.byref(row))
            raise RuntimeError('준비 단계 초기 스레드 없음')
        finally:
            k.CloseHandle(snapshot)

    def stop(self):
        if self.job is not None:
            if not self.k.TerminateJobObject(self.job, 1):
                raise self.c.WinError(self.c.get_last_error())
        elif self.process.poll() is None:
            self.process.kill()
        self.process.wait(timeout=2)

    def close(self):
        if self.job is not None:
            # KILL_ON_JOB_CLOSE는 사용하지 않는다. 성공한 Chrome 실행은 유지해야 한다.
            self.k.CloseHandle(self.job)
            self.job = None

    def __enter__(self):
        return self

    def __exit__(self, kind, value, traceback):
        try:
            if kind is not None:
                self.stop()
        finally:
            self.close()
