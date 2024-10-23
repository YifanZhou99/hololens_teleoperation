import collections
import time
from pathlib import Path

import cv2
import numpy as np
from IPython import display
import openvino as ov
from openvino.tools import mo
import utils as utils

import threading

# Downloads models
import get_detection_model
import get_depth_model
##

core = ov.Core()
device = utils.device_widget()

# Read the network and corresponding weights from a file.
detection_model = core.read_model(model='model/ssdlite_mobilenet_v2_fp16.xml')
depth_model = core.read_model(model='models_ov/depth_anything_v2_metric_vkitti_vits_int8.xml')

# Compile the model for CPU (you can choose manually CPU, GPU etc.)
# or let the engine choose the best available device (AUTO).
compiled_detection_model = core.compile_model(model=detection_model, device_name=device.value)
compiled_depth_model = core.compile_model(model=depth_model,device_name=device.value)

# Get the input and output nodes.
input_layer = compiled_detection_model.input(0)
output_layer = compiled_detection_model.output(0)

# Get the input size.
height, width = list(input_layer.shape)[1:3]

# https://tech.amikelive.com/node-718/what-object-categories-labels-are-in-coco-dataset/
classes = [
    "background",
    "person",
    "bicycle",
    "car",
    "motorcycle",
    "airplane",
    "bus",
    "train",
    "truck",
    "boat",
    "traffic light",
    "fire hydrant",
    "street sign",
    "stop sign",
    "parking meter",
    "bench",
    "bird",
    "cat",
    "dog",
    "horse",
    "sheep",
    "cow",
    "elephant",
    "bear",
    "zebra",
    "giraffe",
    "hat",
    "backpack",
    "umbrella",
    "shoe",
    "eye glasses",
    "handbag",
    "tie",
    "suitcase",
    "frisbee",
    "skis",
    "snowboard",
    "sports ball",
    "kite",
    "baseball bat",
    "baseball glove",
    "skateboard",
    "surfboard",
    "tennis racket",
    "bottle",
    "plate",
    "wine glass",
    "cup",
    "fork",
    "knife",
    "spoon",
    "bowl",
    "banana",
    "apple",
    "sandwich",
    "orange",
    "broccoli",
    "carrot",
    "hot dog",
    "pizza",
    "donut",
    "cake",
    "chair",
    "couch",
    "potted plant",
    "bed",
    "mirror",
    "dining table",
    "window",
    "desk",
    "toilet",
    "door",
    "tv",
    "laptop",
    "mouse",
    "remote",
    "keyboard",
    "cell phone",
    "microwave",
    "oven",
    "toaster",
    "sink",
    "refrigerator",
    "blender",
    "book",
    "clock",
    "vase",
    "scissors",
    "teddy bear",
    "hair drier",
    "toothbrush",
    "hair brush",
]

# Colors for the classes above (Rainbow Color Map).
colors = cv2.applyColorMap(
    src=np.arange(0, 255, 255 / len(classes), dtype=np.float32).astype(np.uint8),
    colormap=cv2.COLORMAP_RAINBOW,
).squeeze()


def process_results(frame, results, thresh=0.6):
    # The size of the original frame.
    h, w = frame.shape[:2]
    # The 'results' variable is a [1, 1, 100, 7] tensor.
    results = results.squeeze()
    boxes = []
    labels = []
    scores = []
    for _, label, score, xmin, ymin, xmax, ymax in results:
        # Create a box with pixels coordinates from the box with normalized coordinates [0,1].
        boxes.append(tuple(map(int, (xmin * w, ymin * h, (xmax - xmin) * w, (ymax - ymin) * h))))
        labels.append(int(label))
        scores.append(float(score))

    # Apply non-maximum suppression to get rid of many overlapping entities.
    # See https://paperswithcode.com/method/non-maximum-suppression
    # This algorithm returns indices of objects to keep.
    indices = cv2.dnn.NMSBoxes(bboxes=boxes, scores=scores, score_threshold=thresh, nms_threshold=0.6)

    # If there are no boxes.
    if len(indices) == 0:
        return []

    # Filter detected objects.
    return [(labels[idx], scores[idx], boxes[idx]) for idx in indices.flatten()]

def draw_boxes(frame, boxes):
    for label, score, box in boxes:
        # Choose color for the label.
        color = tuple(map(int, colors[label]))
        # Draw a box.
        x2 = box[0] + box[2]
        y2 = box[1] + box[3]
        cv2.rectangle(img=frame, pt1=box[:2], pt2=(x2, y2), color=color, thickness=3)

        # Draw a label name inside the box.
        cv2.putText(
            img=frame,
            text=f"{classes[label]} {score:.2f}",
            org=(box[0] + 10, box[1] + 30),
            fontFace=cv2.FONT_HERSHEY_COMPLEX,
            fontScale=frame.shape[1] / 1000,
            color=color,
            thickness=1,
            lineType=cv2.LINE_AA,
        )

    return frame

def draw_depth_boxes(frame, boxes, depths):
    for (label, score, box), depth in zip(boxes, depths):
        # Choose color for the label.
        color = tuple(map(int, colors[label]))
        
        # Draw a box.
        x2 = box[0] + box[2]
        y2 = box[1] + box[3]
        cv2.rectangle(img=frame, pt1=box[:2], pt2=(x2, y2), color=color, thickness=3)

        # Draw a label name, score, and depth inside the box.
        label_text = f"{classes[label]} {score:.2f} Depth: {depth:.2f}m"
        cv2.putText(
            img=frame,
            text=label_text,
            org=(box[0] + 10, box[1] + 30),  # Position slightly inside the box
            fontFace=cv2.FONT_HERSHEY_COMPLEX,
            fontScale=frame.shape[1] / 1000,  # Adjust scale based on image size
            color=color,
            thickness=1,
            lineType=cv2.LINE_AA,
        )

    return frame
#
def estimate_box_depth(metric_depth, boxes, threshold=0.3, stride=2):
    depths = []

    for _, _, box in boxes:
        x, y, w, h = box

        # Get a smaller box in the middle of the bounding box
        new_w = w // 2 
        new_h = h // 2  
        new_x = x + w // 4  
        new_y = y + h // 4 

        # Sample region of interest from depth map with downsampling
        roi = metric_depth[new_y:new_y + new_h:stride, new_x:new_x + new_w:stride]
        roi_depths = roi.flatten()

        median_depth = np.median(roi_depths)
        valid_depths = roi_depths[np.abs(roi_depths - median_depth) < threshold]
        
        if len(valid_depths) > 0:
            avg_depth = np.mean(valid_depths)
        else:
            avg_depth = median_depth
        
        depths.append(avg_depth)

    return depths

# Main processing function to run object detection on a single frame.
def get_object_detection_frame(input_frame):

    # Resize the image and change dims to fit neural network input.
    input_img = cv2.resize(src=input_frame, dsize=(width, height), interpolation=cv2.INTER_AREA)
    # Create a batch of images (size = 1).
    input_img = input_img[np.newaxis, ...]

    # Get the results.
    results = compiled_detection_model([input_img])[output_layer]
    # Get poses from network results.
    boxes = process_results(frame=input_frame, results=results)

    # Draw boxes on a frame.
    frame = draw_boxes(frame=input_frame, boxes=boxes)

    return frame
          
# Main processing function to run object detection with webcam.
def run_object_detection(source=0, flip=False, use_popup=True, skip_first_frames=0):
    player = None
    try:
        # Create a video player to play with target fps.
        player = utils.VideoPlayer(source=source, flip=flip, fps=30, skip_first_frames=skip_first_frames)
        # Start capturing.
        player.start()
        if use_popup:
            title = "Press ESC to Exit"
            cv2.namedWindow(winname=title, flags=cv2.WINDOW_GUI_NORMAL | cv2.WINDOW_AUTOSIZE)

        processing_times = collections.deque()
        while True:
            # Grab the frame.
            frame = player.next()
            if frame is None:
                print("Source ended")
                break
            # If the frame is larger than full HD, reduce size to improve the performance.
            scale = 1280 / max(frame.shape)
            if scale < 1:
                frame = cv2.resize(
                    src=frame,
                    dsize=None,
                    fx=scale,
                    fy=scale,
                    interpolation=cv2.INTER_AREA,
                )

            # Resize the image and change dims to fit neural network input.
            input_img = cv2.resize(src=frame, dsize=(width, height), interpolation=cv2.INTER_AREA)
            # Create a batch of images (size = 1).
            input_img = input_img[np.newaxis, ...]

            # Measure processing time.

            start_time = time.time()
            # Get the results.
            results = compiled_detection_model([input_img])[output_layer]
            stop_time = time.time()
            # Get poses from network results.
            boxes = process_results(frame=frame, results=results)

            # Draw boxes on a frame.
            frame = draw_boxes(frame=frame, boxes=boxes)

            processing_times.append(stop_time - start_time)
            # Use processing times from last 200 frames.
            if len(processing_times) > 200:
                processing_times.popleft()

            _, f_width = frame.shape[:2]
            # Mean processing time [ms].
            processing_time = np.mean(processing_times) * 1000
            fps = 1000 / processing_time
            cv2.putText(
                img=frame,
                text=f"Inference time: {processing_time:.1f}ms ({fps:.1f} FPS)",
                org=(20, 40),
                fontFace=cv2.FONT_HERSHEY_COMPLEX,
                fontScale=f_width / 1000,
                color=(0, 0, 255),
                thickness=1,
                lineType=cv2.LINE_AA,
            )

            # Use this workaround if there is flickering.
            if use_popup:
                cv2.imshow(winname=title, mat=frame)
                key = cv2.waitKey(1)
                # escape = 27
                if key == 27:
                    break
            else:
                # Encode numpy array to jpg.
                _, encoded_img = cv2.imencode(ext=".jpg", img=frame, params=[cv2.IMWRITE_JPEG_QUALITY, 100])
                # Create an IPython image.
                i = display.Image(data=encoded_img)
                # Display the image in this notebook.
                display.clear_output(wait=True)
                display.display(i)
    # ctrl-c
    except KeyboardInterrupt:
        print("Interrupted")
    # any different error
    except RuntimeError as e:
        print(e)
    finally:
        if player is not None:
            # Stop capturing.
            player.stop()
        if use_popup:
            cv2.destroyAllWindows()

def get_depth_frame(input_frame):

    input_tensor, image_size = utils.image_preprocess(input_frame)
    model_out = compiled_depth_model(input_tensor)[0]

    return utils.postprocess(model_out, image_size)

def generate_monocular_point_cloud(depth_frame,focal_length_x,focal_length_y):

    width, height = depth_frame.size

    x, y = np.meshgrid(np.arange(width), np.arange(height))
    x = (x - width / 2) / focal_length_x
    y = (y - height / 2) / focal_length_y
    z = np.array(depth_frame)
    points = np.stack((np.multiply(x, z), np.multiply(y, z), z), axis=-1).reshape(-1, 3)

    return points

# Tried something didn't really make a difference
def get_detection_and_depth_frame(frame):
    input_img = cv2.resize(src=frame, dsize=(width, height), interpolation=cv2.INTER_AREA)
    input_img = input_img[np.newaxis, ...]

    # Detection results storage
    detection_results = {}
    depth_results = {}

    def run_detection_model():
        detection_results['boxes'] = compiled_detection_model(input_img)[output_layer]
        detection_results['processed_boxes'] = process_results(frame=frame, results=detection_results['boxes'])

    def run_depth_model():
        input_tensor, image_size = utils.image_preprocess_depth(frame)
        depth_results['depth'] = compiled_depth_model(input_tensor)[0]
        depth_results['out_frame'], depth_results['metric_depth'] = utils.postprocess_depth(depth_results['depth'], image_size)

    detection_thread = threading.Thread(target=run_detection_model)
    depth_thread = threading.Thread(target=run_depth_model)

    detection_thread.start()
    depth_thread.start()

    detection_thread.join()
    depth_thread.join()

    depths = estimate_box_depth(depth_results['metric_depth'],detection_results['processed_boxes'])
    vis_frame = draw_depth_boxes(depth_results['out_frame'],detection_results['processed_boxes'],depths)

    return vis_frame , detection_results['processed_boxes'], depths , depth_results['out_frame']

def run_detection_and_depth(source=0, flip=False, use_popup=True, skip_first_frames=0):

    player = None
    try:
        # Create a video player to play with target fps.
        player = utils.VideoPlayer(source=source, flip=flip, fps=30, skip_first_frames=skip_first_frames)
        # Start capturing.
        player.start()
        if use_popup:
            title = "Press ESC to Exit"
            cv2.namedWindow(winname=title, flags=cv2.WINDOW_GUI_NORMAL | cv2.WINDOW_AUTOSIZE)

        processing_times = collections.deque()
        while True:
            # Grab the frame.
            frame = player.next()
            if frame is None:
                print("Source ended")
                break
            # If the frame is larger than full HD, reduce size to improve the performance.
            scale = 1280 / max(frame.shape)
            if scale < 1:
                frame = cv2.resize(
                    src=frame,
                    dsize=None,
                    fx=scale,
                    fy=scale,
                    interpolation=cv2.INTER_AREA,
                )

            start_time = time.time()
            frame, _ = get_detection_and_depth_frame(frame)
            stop_time = time.time()

            processing_times.append(stop_time - start_time)
            # Use processing times from last 200 frames.
            if len(processing_times) > 200:
                processing_times.popleft()

            _, f_width = frame.shape[:2]
            # Mean processing time [ms].
            processing_time = np.mean(processing_times) * 1000
            fps = 1000 / processing_time
            cv2.putText(
                img=frame,
                text=f"Inference time: {processing_time:.1f}ms ({fps:.1f} FPS)",
                org=(20, 40),
                fontFace=cv2.FONT_HERSHEY_COMPLEX,
                fontScale=f_width / 1000,
                color=(0, 0, 255),
                thickness=1,
                lineType=cv2.LINE_AA,
            )

            # Use this workaround if there is flickering.
            if use_popup:
                cv2.imshow(winname=title, mat=frame)
                key = cv2.waitKey(1)
                # escape = 27
                if key == 27:
                    break
            else:
                # Encode numpy array to jpg.
                _, encoded_img = cv2.imencode(ext=".jpg", img=frame, params=[cv2.IMWRITE_JPEG_QUALITY, 100])
                # Create an IPython image.
                i = display.Image(data=encoded_img)
                # Display the image in this notebook.
                display.clear_output(wait=True)
                display.display(i)
    # ctrl-c
    except KeyboardInterrupt:
        print("Interrupted")
    # any different error
    except RuntimeError as e:
        print(e)
    finally:
        if player is not None:
            # Stop capturing.
            player.stop()
        if use_popup:
            cv2.destroyAllWindows()


#image_path = 'demo/rgb.png'
#frame = cv2.imread(image_path)


# out_frame_detection = get_object_detection_frame(frame)
# frame = cv2.imread(image_path)
# out_frame_depth = get_detection_and_depth_frame(frame)
# run_object_detection()

# cv2.imshow(winname="test_detection",mat=out_frame_detection)
# cv2.imshow(winname="test_depth",mat=out_frame_depth)
# cv2.waitKey()
# cv2.destroyAllWindows()

run_detection_and_depth()