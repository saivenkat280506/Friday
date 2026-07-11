import mss
import base64
from PIL import Image
import io
import cv2
import numpy as np

def capture_screen_base64(draw_boxes: bool = True) -> str:
    """Captures the primary screen, optionally highlights UI elements, resizes, and returns base64 jpeg."""
    with mss.mss() as sct:
        monitor = sct.monitors[1] # Primary monitor
        sct_img = sct.grab(monitor)
        
        # Convert raw BGRA to numpy array for OpenCV
        img_np = np.array(sct_img)
        
        if draw_boxes:
            # Convert to grayscale
            gray = cv2.cvtColor(img_np, cv2.COLOR_BGRA2GRAY)
            # Edge detection to find UI outlines
            edges = cv2.Canny(gray, 50, 150)
            
            # Find contours
            contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            
            for cnt in contours:
                x, y, w, h = cv2.boundingRect(cnt)
                # Filter noise (like text characters or tiny icons) and massive boxes (whole screen)
                if 20 < w < 400 and 20 < h < 100:
                    cv2.rectangle(img_np, (x, y), (x+w, y+h), (0, 0, 255, 255), 2)
                    
        # Convert back to PIL Image (discarding alpha channel)
        img = Image.fromarray(cv2.cvtColor(img_np, cv2.COLOR_BGRA2RGB))
        
        # Resize to maintain proportion but reduce LLM token overhead
        max_dim = (1024, 576) if not draw_boxes else (1280, 720)
        img.thumbnail(max_dim)

        # Slightly lower quality for passive captures — faster upload, same accuracy
        jpeg_quality = 68 if not draw_boxes else 75
        
        # Compress and save to BytesIO buffer
        buffer = io.BytesIO()
        img.save(buffer, format="JPEG", quality=jpeg_quality, optimize=True)
        
        return base64.b64encode(buffer.getvalue()).decode('utf-8')
