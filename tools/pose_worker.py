"""Isolated MediaPipe Tasks worker: local bounded frames, no account or network."""
import hashlib,json,math,sys
from pathlib import Path


def analyze(frames,models,frame_root):
    import mediapipe as mp
    if not isinstance(frames,list) or not 1<=len(frames)<=32:raise ValueError('Bounded frame list required')
    models,frame_root=Path(models).resolve(strict=True),Path(frame_root).resolve(strict=True)
    expected={'pose_landmarker_lite.task':'59929e1d1ee95287735ddd833b19cf4ac46d29bc7afddbbf6753c459690d574a',
              'hand_landmarker.task':'fbc2a30080c3c557093b5ddfc334698132eb341044ccee322ccf8bcf3607cde1'}
    for name,checksum in expected.items():
        model=models/name
        if model.is_symlink() or model.stat().st_size>20*1024**2 or hashlib.sha256(model.read_bytes()).hexdigest()!=checksum:
            raise PermissionError('MediaPipe model integrity check failed')
    pose_options=mp.tasks.vision.PoseLandmarkerOptions(base_options=mp.tasks.BaseOptions(
        model_asset_buffer=(models/'pose_landmarker_lite.task').read_bytes()),num_poses=1,min_pose_detection_confidence=.45)
    hand_options=mp.tasks.vision.HandLandmarkerOptions(base_options=mp.tasks.BaseOptions(
        model_asset_buffer=(models/'hand_landmarker.task').read_bytes()),num_hands=2,min_hand_detection_confidence=.45)
    pose_hits=hand_hits=0;wrists=[];total=0
    with mp.tasks.vision.PoseLandmarker.create_from_options(pose_options) as pose, \
         mp.tasks.vision.HandLandmarker.create_from_options(hand_options) as hands:
        for frame in frames:
            file=Path(frame['path'])
            if file.is_symlink() or file.suffix.lower() not in {'.png','.jpg','.jpeg'} or not file.resolve().is_relative_to(frame_root):
                raise PermissionError('Frame is outside the analysis cache')
            size=file.stat().st_size;total+=size
            if size>16*1024**2 or total>64*1024**2:raise ValueError('Analysis frames exceed byte limit')
            # Native Windows path readers in OpenCV/MediaPipe reject some Unicode paths.
            from PIL import Image
            import numpy as np
            with Image.open(file) as loaded:
                if loaded.width*loaded.height>16_777_216:raise ValueError('Analysis image exceeds pixel limit')
                pixels=np.asarray(loaded.convert('RGB'))
            image=mp.Image(image_format=mp.ImageFormat.SRGB,data=np.ascontiguousarray(pixels))
            pr,hr=pose.detect(image),hands.detect(image)
            if pr.pose_landmarks:
                pose_hits+=1;lm=pr.pose_landmarks[0]
                if len(lm)>16:wrists.append((float(frame['time']),(lm[15].x+lm[16].x)/2,(lm[15].y+lm[16].y)/2))
            if hr.hand_landmarks:hand_hits+=1
    motion=sum(math.hypot(b[1]-a[1],b[2]-a[2]) for a,b in zip(wrists,wrists[1:]))
    return {'available':True,'used':True,'engine':'isolated MediaPipe Tasks',
        'sample_frames':len(frames),'pose_frames':pose_hits,'hand_frames':hand_hits,
        'wrist_motion_index':round(motion,3),'person_presence_ratio':round(pose_hits/len(frames),2),
        'hand_presence_ratio':round(hand_hits/len(frames),2),
        'note':'Local pose/hand continuity only; no identity recognition or remote upload.'}


if __name__=='__main__':
    request=json.loads(sys.stdin.buffer.read(128_001))
    print(json.dumps(analyze(request['frames'],sys.argv[1],sys.argv[2])))
