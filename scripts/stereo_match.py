"""Run horizontal SGBM in either rectified epipolar direction."""
import cv2

def match_pair(images, axis, minimum, count, parameters):
    if axis not in (0,1):raise ValueError('axis must be horizontal or vertical')
    pair=images if axis==0 else [im.T.copy() for im in images]
    reverse_min=-minimum-count+1
    disparity=cv2.StereoSGBM_create(minDisparity=minimum,numDisparities=count,**parameters).compute(*pair).astype('float32')/16
    reverse=cv2.StereoSGBM_create(minDisparity=reverse_min,numDisparities=count,**parameters).compute(pair[1],pair[0]).astype('float32')/16
    return (disparity,reverse) if axis==0 else (disparity.T,reverse.T)
