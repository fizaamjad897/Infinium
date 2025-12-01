from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import joblib
import numpy as np
from typing import Literal
from keras.models import load_model
from fastapi.middleware.cors import CORSMiddleware


# Initialize FastAPI app
app = FastAPI(
    title="Heart Disease Prediction API",
    description="An API to predict heart disease based on patient data using a pre-trained Keras model.",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # or you can specify allowed origins like ["http://example.com"]
    allow_credentials=True,
    allow_methods=["*"],  # Allows all methods like GET, POST, PUT, DELETE, etc.
    allow_headers=["*"],
)

# Load the trained model with exception handling
try:
    model = load_model('best_nn_model.keras')  # Loading the trained Keras model
    scaler = joblib.load("scaler.pkl")
except Exception as e:
    raise RuntimeError("Model file not found or corrupted. Ensure file exists.") from e

# Input schema using Pydantic
class HeartDiseaseInput(BaseModel):
    Age: float
    Sex: Literal[0, 1]  
    RestingBP: float
    Cholesterol: float
    FastingBS: Literal[0, 1]
    MaxHR: float
    ExerciseAngina: Literal[0, 1]
    Oldpeak: float
    Smoking: Literal[0, 1]
    HyperTension: Literal[0, 1]
    Diabetes: Literal[0, 1]
    FamilyHistory: Literal[0, 1]
    ChestPainType_ASY: Literal[0, 1]
    ChestPainType_ATA: Literal[0, 1]
    ChestPainType_NAP: Literal[0, 1]
    ChestPainType_TA: Literal[0, 1]
    RestingECG_LVH: Literal[0, 1]
    RestingECG_Normal: Literal[0, 1]
    RestingECG_ST: Literal[0, 1]
    ST_Slope_Down: Literal[0, 1]
    ST_Slope_Flat: Literal[0, 1]
    ST_Slope_Up: Literal[0, 1]

# Output schema for the prediction response
class PredictionOutput(BaseModel):
    prediction: Literal["No", "Maybe", "Yes"]
    probability: float

@app.post("/predict", response_model=PredictionOutput, summary="Predict Heart Disease")
def predict(request: HeartDiseaseInput):
    """
    Predict the likelihood of heart disease based on patient input.
    """
    try:
        # Convert request data to a NumPy array
        input_data = np.array([[
            request.Age,
            request.Sex,
            request.RestingBP,
            request.Cholesterol,
            request.FastingBS,
            request.MaxHR,
            request.ExerciseAngina,
            request.Oldpeak,
            request.Smoking,
            request.HyperTension,
            request.Diabetes,
            request.FamilyHistory,
            request.ChestPainType_ASY,
            request.ChestPainType_ATA,
            request.ChestPainType_NAP,
            request.ChestPainType_TA,
            request.RestingECG_LVH,
            request.RestingECG_Normal,
            request.RestingECG_ST,
            request.ST_Slope_Down,
            request.ST_Slope_Flat,
            request.ST_Slope_Up
        ]])

        # Identify indices of numerical features
        numerical_indices = [0, 2, 3, 5, 7]  # Indices corresponding to Age, RestingBP, etc.

        # Apply scaling to numerical features
        input_data[0, numerical_indices] = scaler.transform(input_data[0, numerical_indices].reshape(1, -1))
        print(input_data)

        # Get predictions from the Keras model
        predictions = model.predict(input_data)[0]
        print(predictions)

        # Extract the class with the highest probability
        predicted_class = np.argmax(predictions)
        probability = predictions[predicted_class]

        # Map prediction to readable result
        result_map = {0: "No", 1: "Maybe", 2: "Yes"}
        result = result_map.get(predicted_class, "Unknown")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Model prediction failed: {str(e)}")

    return {"prediction": result, "probability": round(float(probability), 4)}


# Root endpoint for health check
@app.get("/", summary="Health Check", tags=["Root"])
def root():
    return {"message": "Heart Disease Prediction API is running. Use '/predict' to make predictions."}