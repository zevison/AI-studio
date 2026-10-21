from pathlib import Path
import pytest
from desktop.process_ownership import BrowserProcessOwner,browser_identity_matches


class FakeProcesses:
    def __init__(self,expected):
        self.expected=expected;self.rows=[];self.births={};self.images={};self.dead=set();self.closed=[];self.terminated=[]
    def add(self,pid,parent,birth,image=None):
        self.rows.append({'pid':pid,'parent':parent,'name':'msedgewebview2.exe'});self.births[pid]=birth;self.images[pid]=image or self.expected
    def current_birth(self):return 1000
    def snapshot(self):return self.rows[:]
    def open(self,pid):return pid
    def birth(self,handle):return self.births[handle]
    def image(self,handle):return self.images[handle]
    def exited(self,handle):return handle in self.dead
    def close(self,handle):self.closed.append(handle)
    def terminate(self,handle):self.terminated.append(handle);self.dead.add(handle)


class FakeJob:
    def __init__(self,api,deny=False):self.api=api;self.deny=deny;self.members=[];self.closed=False
    def add_process(self,handle):
        if self.deny:raise OSError('nested job unavailable')
        self.members.append(handle)
    def close(self):self.closed=True;self.api.dead.update(self.members)


def owner(tmp_path,deny=False):
    image=tmp_path/'runtime/msedgewebview2.exe';api=FakeProcesses(image);job=FakeJob(api,deny)
    target=BrowserProcessOwner(image,job,tmp_path/'report.json',api=api,owner_pid=10,owner_birth=1000,start=False)
    return target,api,job


def test_only_exact_runtime_owned_parent_and_new_birth_are_accepted(tmp_path):
    target,api,job=owner(tmp_path)
    api.add(11,10,1100);api.add(12,11,1200)
    api.add(20,99,1300)  # Same image, another application's parent.
    api.add(21,10,500)   # Old orphan associated with a reused parent PID.
    api.add(22,10,1300,tmp_path/'other/msedgewebview2.exe')
    api.add(23,11,1050)  # Child cannot predate its held parent.
    target.capture()
    assert set(target.members)=={11,12}
    report=target.close(grace=0,final_wait=0)
    assert report['remaining_after_cleanup']==[]
    assert set(job.members)=={11,12}
    assert not {20,21,22,23}.intersection(api.terminated)


def test_nested_job_failure_uses_verified_held_handles_only(tmp_path):
    target,api,job=owner(tmp_path,deny=True)
    api.add(11,10,1100);api.add(20,99,1300)
    target.capture();report=target.close(grace=0,final_wait=0)
    assert api.terminated==[11] and 20 not in api.terminated
    assert report['remaining_after_cleanup']==[]
    assert any(row['stage']=='job-fallback' for row in report['events'])


def test_exited_parent_handle_remains_until_its_children_are_discovered(tmp_path):
    target,api,job=owner(tmp_path)
    api.add(11,10,1100);target.capture();api.dead.add(11)
    api.add(12,11,1200);target.capture()
    assert set(target.members)=={11,12}
    api.dead.add(12);api.rows=[];target.capture()
    assert not target.members
    assert {11,12}<=set(api.closed)
    target.close(grace=0,final_wait=0)


@pytest.mark.parametrize('birth,parent_birth',[(999,1000),(1000,None),(None,1000)])
def test_invalid_creation_identity_refused(tmp_path,birth,parent_birth):
    image=tmp_path/'msedgewebview2.exe'
    assert not browser_identity_matches(image,image,birth,parent_birth)


def test_snapshot_failure_still_closes_job_and_owned_handles(tmp_path):
    target,api,job=owner(tmp_path)
    api.add(11,10,1100);target.capture()
    def fail():raise OSError('snapshot unavailable')
    api.snapshot=fail
    report=target.close(grace=0,final_wait=0)
    assert job.closed and 11 in api.closed
    assert report['remaining_after_cleanup']==[]
