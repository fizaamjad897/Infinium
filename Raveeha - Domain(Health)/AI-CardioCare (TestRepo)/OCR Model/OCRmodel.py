from fastapi import FastAPI, File, UploadFile
from fastapi.responses import JSONResponse
import cv2
import pytesseract
from PIL import Image
import io
import re
import numpy as np
from fastapi.middleware.cors import CORSMiddleware

# Initialize FastAPI
app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # or you can specify allowed origins like ["http://example.com"]
    allow_credentials=True,
    allow_methods=["*"],  # Allows all methods like GET, POST, PUT, DELETE, etc.
    allow_headers=["*"],
)


# Specify the path to the Tesseract executable
pytesseract.pytesseract.tesseract_cmd = r'C:\Users\DELL\AppData\Local\Programs\Tesseract-OCR\tesseract.exe'

# Function to extract required values from text using regular expressions
def extract_values(text):
    # Extracting Age
    age = re.search(r'Age:\s*(\d+)', text)
    age = age.group(1) if age else "Not found"

    # Extracting Sex
    sex = re.search(r'Sex:\s*(\w+)', text)
    sex = sex.group(1) if sex else "Not found"

    # Extracting Diabetes
    diabetes = re.search(r'Diabetes\s*(Yes|No)', text)
    diabetes = diabetes.group(1) if diabetes else "Not found"

    # Extracting Hypertension
    hypertension = re.search(r'Hypertension\s*(Yes|No)', text)
    hypertension = hypertension.group(1) if hypertension else "Not found"

    # Extracting Smoking
    smoking = re.search(r'Smoking\s*(Yes|No)', text)
    smoking = smoking.group(1) if smoking else "Not found"

    # Extracting Family History
    family_history = re.search(r'Family History\s*(Yes|No)', text)
    family_history = family_history.group(1) if family_history else "Not found"

    # Extracting ChestPainType
    chest_pain_type = re.search(r'ChestPainType\s*(\w+)', text)
    chest_pain_type = chest_pain_type.group(1) if chest_pain_type else "Not found"

    # Extracting ExerciseAngina
    exercise_angina = re.search(r'ExerciseAngina\s*(\w+)', text)
    exercise_angina = exercise_angina.group(1) if exercise_angina else "Not found"

    # Extracting ST_Slope
    st_slope = re.search(r'ST_Slope\s*(\w+)', text)
    st_slope = st_slope.group(1) if st_slope else "Not found"

    # Extracting RestingBP
    resting_bp = re.search(r'RestingBP\s*(\d+)', text)
    resting_bp = resting_bp.group(1) if resting_bp else "Not found"

    # Extracting MaxHR
    max_hr = re.search(r'MaxHR\s*(\d+)', text)
    max_hr = max_hr.group(1) if max_hr else "Not found"

    # Extracting Oldpeak
    oldpeak = re.search(r'Oldpeak\s*(\d+)', text)
    oldpeak = oldpeak.group(1) if oldpeak else "Not found"

    # Extracting Cholesterol
    cholesterol = re.search(r'Cholesterol\s*(\d+)', text)
    cholesterol = cholesterol.group(1) if cholesterol else "Not found"

    # Extracting FastingBS
    fasting_bs = re.search(r'FastingBS\s*(\d+)', text)
    fasting_bs = fasting_bs.group(1) if fasting_bs else "Not found"

    # Extracting RestingECG
    resting_ecg = re.search(r'RestingECG\s*(\w+)', text)
    resting_ecg = resting_ecg.group(1) if resting_ecg else "Not found"

    # Return the extracted values as a dictionary
    return {
        "Age": age,
        "Sex": sex,
        "Diabetes": diabetes,
        "Hypertension": hypertension,
        "Smoking": smoking,
        "Family History": family_history,
        "ChestPainType": chest_pain_type,
        "ExerciseAngina": exercise_angina,
        "ST_Slope": st_slope,
        "RestingBP": resting_bp,
        "MaxHR": max_hr,
        "Oldpeak": oldpeak,
        "Cholesterol": cholesterol,
        "FastingBS": fasting_bs,
        "RestingECG": resting_ecg,
    }

# Endpoint for image upload and text extraction
@app.post("/extract_values/")
async def extract_values_from_image(file: UploadFile = File(...)):
    # Convert the uploaded image to a format suitable for OpenCV
    image_data = await file.read()
    image = Image.open(io.BytesIO(image_data))
    open_cv_image = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)

    # Option 1: Extract text without preprocessing (best result)
    # extracted_text_no_preprocessing = pytesseract.image_to_string(img)

    # print("Extracted Text (Without Preprocessing):")
    # print(extracted_text_no_preprocessing)

    # Option 2: Apply preprocessing techniques (gray scaling, noise removal, etc.)

    # # Convert the image to grayscale
    # gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    # # Option 2.1: Adaptive Thresholding (better for varying light conditions)
    # adaptive_thresh = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
    #                                        cv2.THRESH_BINARY, 11, 2)

    # # Option 2.2: Gaussian Blur (removes noise, useful for cleaner text)
    # blur = cv2.GaussianBlur(gray, (5, 5), 0)

    # # Option 2.3: Otsu's Binarization (thresholding for clearer text)
    # _, thresh = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    # # Option 2.4: Dilation (optional, makes text bolder)
    # kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    # dilated = cv2.dilate(thresh, kernel, iterations=1)

    # # Use OCR to extract text from the preprocessed image (choose one of the options below)
    # extracted_text_with_preprocessing = pytesseract.image_to_string(dilated)

    # print("\nExtracted Text (With Preprocessing):")
    # print(extracted_text_with_preprocessing)
    # Resize the image for better OCR accuracy
    img_resized = cv2.resize(open_cv_image, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)

    # Perform OCR on the image
    extracted_text_resized = pytesseract.image_to_string(img_resized)
    print(extracted_text_resized)

    # Extract the values from the OCR result
    extracted_values = extract_values(extracted_text_resized)

    # Return the extracted values as a JSON response
    return JSONResponse(content=extracted_values)

