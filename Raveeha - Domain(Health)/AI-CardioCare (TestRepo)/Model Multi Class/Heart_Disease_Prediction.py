# Importing libraries
import os
os.environ['TF_ENABLE_ONEDNN_OPTS'] = '0'
import pandas as pd
import numpy as np
import seaborn as sns
import matplotlib.pyplot as plt
from sklearn.preprocessing import StandardScaler, LabelEncoder
from sklearn.cluster import KMeans
from sklearn.model_selection import KFold
from imblearn.over_sampling import SMOTE
from sklearn.model_selection import train_test_split, cross_val_score
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import RandomForestClassifier
from sklearn.svm import SVC
from sklearn.metrics import classification_report, confusion_matrix, f1_score, accuracy_score
from sklearn.model_selection import GridSearchCV
import xgboost as xgb
from keras.models import Sequential
from keras.layers import Dense, Dropout
from keras.callbacks import EarlyStopping
from keras.regularizers import l2
from keras.callbacks import EarlyStopping, ReduceLROnPlateau
import pickle
import joblib
import tensorflow as tf
import random

random.seed(42)
np.random.seed(42)
tf.random.set_seed(42)


# Load dataset
file_path = r'F:\5th Semester\AI\Theory\AI Project\Model Multi Class\Final Heart Dataset.csv'
heart_df = pd.read_csv(file_path)

# Examine dataset
print("Dataset Head:")
print(heart_df.head())
print("\nDataset Info:")
print(heart_df.info())
print("\nDataset Describe:")
print(heart_df.describe())

# Handle missing values
if heart_df.isnull().sum().any():
    print("\nMissing values detected. Filling missing values with mean for numeric and mode for categorical columns.")
    for col in heart_df.columns:
        if heart_df[col].dtype in ['float64', 'int64']:
            heart_df[col].fillna(heart_df[col].mean(), inplace=True)
        else:
            heart_df[col].fillna(heart_df[col].mode()[0], inplace=True)
else:
    print("\nNo missing values detected.")


#Applying (ONE HOT ENCODING) to features like Chestpain , RestingECG & ST-Slope as they have nominal (non-ordered) categorical variables
#For sex , exercise angina , smoking and family history doing (LABEL ENCODING)

# Initialize LabelEncoder
label_encoders = {}

# Columns for Label Encoding
label_encode_columns = ['Sex', 'ExerciseAngina', 'Smoking', 'FamilyHistory' , 'HyperTension']

# Columns for One-Hot Encoding
one_hot_encode_columns = ['ChestPainType', 'RestingECG', 'ST_Slope']

# Apply Label Encoding
for col in label_encode_columns:
    print(f"Label Encoding column: {col}")
    le = LabelEncoder()
    heart_df[col] = le.fit_transform(heart_df[col])
    label_encoders[col] = le
    print(f"Mapping for {col}: {dict(zip(le.classes_, le.transform(le.classes_)))}")

# Apply One-Hot Encoding
heart_df = pd.get_dummies(heart_df, columns=one_hot_encode_columns, drop_first=False)
for col in heart_df.columns:
    if heart_df[col].dtype == 'bool':
        heart_df[col] = heart_df[col].astype(int)

# Display the transformed DataFrame
print(heart_df.head())


#REMOVING THE OUTLIERS
#WE SHOULD REMOVE THE OUTLIERS WHEN:
# You suspect measurement errors or data entry mistakes.
# Your model's performance improves after removing outliers.

# SKIP IT:
# Outliers are meaningful (e.g., patients with extremely high cholesterol may have a higher risk of heart disease).
# Removing outliers leads to data imbalance (e.g., under-representation of specific classes).
numerical_columns = ['Age', 'RestingBP', 'Cholesterol', 'MaxHR', 'Oldpeak']

# Function to remove outliers
def remove_outliers(df, columns):
    for col in columns:
        Q1 = df[col].quantile(0.25)  # 1st Quartile
        Q3 = df[col].quantile(0.75)  # 3rd Quartile
        IQR = Q3 - Q1  # Interquartile Range
        lower_bound = Q1 - 1.5 * IQR
        upper_bound = Q3 + 1.5 * IQR
        # Filter out rows with outliers
        df = df[(df[col] >= lower_bound) & (df[col] <= upper_bound)]
    return df

# Remove outliers from the DataFrame
heart_df_cleaned = remove_outliers(heart_df, numerical_columns)

# Box plot to visualize outliers
plt.figure(figsize=(15, 8))
for i, col in enumerate(numerical_columns, 1):
    plt.subplot(2, 3, i)  # Adjust the grid if you add/remove columns
    sns.boxplot(data=heart_df, y=col, color='#600000')
    plt.title(f'Box Plot for {col}')
plt.tight_layout()
plt.show()

# Display cleaned DataFrame info
print(f"Original DataFrame shape: {heart_df.shape}")
print(f"Cleaned DataFrame shape: {heart_df_cleaned.shape}")

#(WE HAVE BOTH CLEANED AND ORIGINAL DATASET IN THE END WE WILL SEE THE ACCURACY AND THEN FIND THAT WHICH DF TO USE)

#SCALING THE DATA SET
#Reasons to Scale Our Data 
# Uniform Feature Contribution: Features like RestingBP, Cholesterol, and MaxHR have different scales and units.Without scaling, features with larger numerical ranges may disproportionately affect the model, leading to biased learning.
# Improved Optimization: Neural networks use gradient-based optimization (e.g., backpropagation), which converges faster when input features are standardized 
# Avoiding Bias: Neural networks treat all inputs equally if they are scaled to similar ranges, leading to a more balanced model.


# Standardization (Z-score normalization) is suitable here because:
# Neural networks perform better when features are centered and normalized.
# The dataset does not appear to have severe outliers in these features, making standardization more appropriate than min-max scaling




#HERE DOING SCALING , CLUSTERING  AND CLASS IMBALANCE
#(WE HAVE BOTH CLEANED AND ORIGINAL DATASET IN THE END WE WILL SEE THE ACCURACY AND THEN FIND THAT WHICH DF TO USE)

#SCALING THE DATA SET
#Reasons to Scale Our Data 
# Uniform Feature Contribution: Features like RestingBP, Cholesterol, and MaxHR have different scales and units.Without scaling, features with larger numerical ranges may disproportionately affect the model, leading to biased learning.
# Improved Optimization: Neural networks use gradient-based optimization (e.g., backpropagation), which converges faster when input features are standardized 
# Avoiding Bias: Neural networks treat all inputs equally if they are scaled to similar ranges, leading to a more balanced model.
# Standardization (Z-score normalization) is suitable here because:
# Neural networks perform better when features are centered and normalized.
# The dataset does not appear to have severe outliers in these features, making standardization more appropriate than min-max scaling


#Converting our Binary classification Model to Multi classification
#Instead of relying only on one feature Oldpeak, we consider using a more sophisticated approach that includes multiple features. Creating clusters or thresholds across key features to derive meaningful classes.


#Now my classes are balanced I have done undersampling & oversampling by using smote (Synthetic Minority Oversampling Technique)
# Before SMOTE: {np.int64(0): np.int64(262), np.int64(1): np.int64(147), np.int64(2): np.int64(325)}
# After SMOTE: {np.int64(0): np.int64(325), np.int64(1): np.int64(325), np.int64(2): np.int64(325)}

#Now I'll apply the model and see the f1score recall and precision and check if my model isnt overfitting
# Since SMOTE introduces synthetic samples, there is a slight risk of overfitting if the model memorizes these samples. To mitigate this:
# Use techniques like cross-validation.
# Ensure that synthetic samples are only created for the training data, not the test set.

# 1. Compute the correlation matrix for numerical features
correlation_matrix = heart_df.corr()
plt.figure(figsize=(10, 6))
sns.heatmap(correlation_matrix, annot=True, cmap='coolwarm', fmt='.2f', linewidths=0.5)
plt.title('Correlation Matrix of Numerical Features')
plt.show()

# 1. Clustering to create 'HeartDiseaseMulticlass'
# Apply clustering first
features_for_clustering = ['Oldpeak', 'MaxHR', 'Cholesterol', 'ExerciseAngina', 'ChestPainType_ASY', 'ST_Slope_Flat']
clustering_data = heart_df[features_for_clustering]

# Apply KMeans clustering
kmeans = KMeans(n_clusters=3, random_state=42)
heart_df['HeartDiseaseMulticlass'] = kmeans.fit_predict(clustering_data)

# Map cluster labels to classes based on domain knowledge
cluster_mapping = {0: 'No', 1: 'Maybe', 2: 'Yes'}
heart_df['HeartDiseaseMulticlass'] = heart_df['HeartDiseaseMulticlass'].map(cluster_mapping)

# 2. Prepare the features and target for training
X = heart_df.drop(['HeartDisease', 'HeartDiseaseMulticlass'], axis=1)  # Drop both target columns
y = heart_df['HeartDiseaseMulticlass']  # Target is now 'HeartDiseaseMulticlass'

# 3. Encode the target labels
label_encoder = LabelEncoder()
y_encoded = label_encoder.fit_transform(y)

combined_df = X.copy()
combined_df['y_encoded'] = y_encoded

# Save the combined DataFrame to a CSV file
combined_df.to_csv("features_with_target.csv", index=False)

# 4. Split the data into train and test sets
X_train, X_test, y_train, y_test = train_test_split(X, y_encoded, test_size=0.2, random_state=42)

# 5. Scaling the data
# Select numerical features for scaling
numerical_features = ["Age", "RestingBP", "Cholesterol", "MaxHR", "Oldpeak"]
scaler = StandardScaler()

# Fit the scaler on the training set and transform both the train and test sets
X_train_scaled = scaler.fit_transform(X_train[numerical_features])
X_test_scaled = scaler.transform(X_test[numerical_features])

# Replace the numerical features in both training and testing data with their scaled versions
X_train[numerical_features] = X_train_scaled
X_test[numerical_features] = X_test_scaled

# Save the scaler to use later during inference
#joblib.dump(scaler, "scaler.pkl")

# 6. Handle class imbalance using SMOTE
smote = SMOTE(random_state=42)
X_train_resampled, y_train_resampled = smote.fit_resample(X_train, y_train)

# Print class distribution before and after SMOTE
print("Before SMOTE:", dict(zip(*np.unique(y_train, return_counts=True))))
print("After SMOTE:", dict(zip(*np.unique(y_train_resampled, return_counts=True))))




#Now my classes are balanced I have done undersampling & oversampling by using smote (Synthetic Minority Oversampling Technique)
# Before SMOTE: {np.int64(0): np.int64(262), np.int64(1): np.int64(147), np.int64(2): np.int64(325)}
# After SMOTE: {np.int64(0): np.int64(325), np.int64(1): np.int64(325), np.int64(2): np.int64(325)}

#Now I'll apply the model and see the f1score recall and precision and check if my model isnt overfitting
# Since SMOTE introduces synthetic samples, there is a slight risk of overfitting if the model memorizes these samples. To mitigate this:
# Use techniques like cross-validation.
# Ensure that synthetic samples are only created for the training data, not the test set.


#MODEL SELECTION
# Logistic Regression (Multinomial):
# A good baseline model to compare with others.
# Use the solver='lbfgs' or solver='sag' with a multinomial setting.

# Random Forest:
# Handles multiclass classification well and is less prone to overfitting due to its ensemble nature.
# Use class_weight='balanced' for imbalanced datasets, although SMOTE has already addressed the imbalance.

# Gradient Boosting Models:
# XGBoost or LightGBM:
# These are powerful for structured data and handle multiclass problems effectively.
# Tune hyperparameters like max_depth, learning_rate, and n_estimators for better performance.

# Support Vector Machine (SVM):
# Use with a nonlinear kernel (e.g., RBF kernel).
# May require additional preprocessing due to its sensitivity to feature scaling.

# Neural Network:
# Use a feed-forward neural network with:
# An input layer matching the number of features.
# One or two hidden layers with ReLU activation.
# An output layer with softmax activation for multiclass classification.
# Use early stopping and dropout layers to prevent overfitting.


#Accuracy formula is..how many we got right / total sample
#Pecision is...out of all the Predicitions..how many we got right..suppose we predict 5 are no..and only 3 of them are right..so 3/5 (TP/(TP+FP))
#Recall is...Out of all the Truth values..how many we got right...suppose the truth was there were 6 healthy patients and we got 4 right..4/6(TP/(TP+FN))
#F1Score is...2*((precision.recall)/precision+recall))


#TRAINING MULTIPLE MODELS
# Logistic Regression
    # log_reg = LogisticRegression(multi_class='multinomial', solver='lbfgs', random_state=42)
    # log_reg.fit(X_train_resampled, y_train_resampled)
    # y_pred_log_reg = log_reg.predict(X_test)

    # # Evaluation
    # print("Logistic Regression Evaluation:")
    # print(classification_report(y_test, y_pred_log_reg))
    # print(confusion_matrix(y_test, y_pred_log_reg))

#(Logistic Regression Evaluation: 0.97)


# Random Forest
    # rf = RandomForestClassifier(n_estimators=100, random_state=42, class_weight='balanced')
    # rf.fit(X_train_resampled, y_train_resampled)
    # y_pred_rf = rf.predict(X_test)

    # # Evaluation
    # print("Random Forest Evaluation:")
    # print(classification_report(y_test, y_pred_rf))
    # print(confusion_matrix(y_test, y_pred_rf))

#(Random Forest Evaluation: 0.92)

# XGBoost
    # xgb_model = xgb.XGBClassifier(use_label_encoder=False, eval_metric='mlogloss', random_state=42)
    # xgb_model.fit(X_train_resampled, y_train_resampled)
    # y_pred_xgb = xgb_model.predict(X_test)

    # # Evaluation
    # print("XGBoost Evaluation:")
    # print(classification_report(y_test, y_pred_xgb))
    # print(confusion_matrix(y_test, y_pred_xgb))

#(XGBoost Evaluation:0.94)


# Support Vector Machine
    # svm = SVC(kernel='rbf', random_state=42)
    # svm.fit(X_train_resampled, y_train_resampled)
    # y_pred_svm = svm.predict(X_test)

    # # Evaluation
    # print("SVM Evaluation:")
    # print(classification_report(y_test, y_pred_svm))
    # print(confusion_matrix(y_test, y_pred_svm))

#(SVM Evaluation:0.93)


# Neural Network
nn_model = Sequential()
nn_model.add(Dense(64, input_dim=X_train.shape[1], activation='relu'))
nn_model.add(Dropout(0.5))
nn_model.add(Dense(32, activation='relu'))
nn_model.add(Dense(3, activation='softmax'))  # 3 classes

# Compile the model
nn_model.compile(optimizer='adam', loss='sparse_categorical_crossentropy', metrics=['accuracy'])

# Early Stopping
early_stopping = EarlyStopping(monitor='val_loss', patience=5)

# Train the model
nn_model.fit(X_train_resampled, y_train_resampled, epochs=50, batch_size=32, validation_data=(X_test, y_test), callbacks=[early_stopping])

# Evaluation
y_pred_nn = np.argmax(nn_model.predict(X_test), axis=1)

print("Neural Network Evaluation:")
print(classification_report(y_test, y_pred_nn))
print(confusion_matrix(y_test, y_pred_nn))


sns.heatmap(confusion_matrix(y_test, y_pred_nn), annot=True, fmt='d', cmap='Blues')
plt.title("Neural Network Confusion Matrix")
plt.show()


#WHY WE CHOOSE NEURAL NETWORK????
# it is effectively balancing precision and recall while making accurate predictions.
# Neural networks can easily be adjusted for various types of data and can learn from complex patterns, making them useful for real-world problems like ours.remove_outliers

# Although it also performed well with an F1-score of 0.9784 and accuracy of 0.98, Logistic Regression might not capture non-linear relationships as well as the Neural Network, especially in a dataset with intricate patterns.
#  While Random Forest performed well (F1-score 0.9236), its performance is slightly lower compared to Neural Networks and Logistic Regression. Its a great choice for feature importance, but it might not capture complex interactions in the data as effectively as a neural network.
#  Both models also performed well, but the Neural Network is still slightly better in terms of F1-score. XGBoost and SVM are robust models, but they may require more tuning to match the performance of a neural network.


#CHECKING THAT WETHER OUT MODEL IS OVERFIT?
#WHAT IS MODEL OVERFITTING..
# Model overfitting occurs when a machine learning model learns not only the underlying patterns
#  in the training data but also the noise and details specific to that data. As a result,
#  the model performs very well on the training data but fails to generalize to new,
#  unseen data, leading to poor performance on the test set or in real-world applications.
# The model may be trained for too many iterations, allowing it to fit the training data too closely.

# The model has a very low error on the training set but performs poorly on the test set.
# The model learns random noise or irrelevant patterns in the data.



#ALTHOUGH WE ARE ALREADY USING EARLY_STOPPING TO PREVENT THE OVERFITTING (For iterative algorithms like neural networks, stop training when the performance on a validation set starts to degrade.)
#But still going through the CROSS-VALIDATION


# Initialize KFold
kf = KFold(n_splits=5, shuffle=True, random_state=42)  # 5-fold cross-validation

# Store results
validation_losses = []
train_losses = []
accuracies = []
max_epochs = 0
best_model = None
best_val_loss = np.inf  # Initialize with a large value to track the best model

# Loop through each fold
for fold, (train_index, val_index) in enumerate(kf.split(X_train_resampled)):
    print(f"Starting fold {fold + 1}...")

    # Split data
    X_train_fold = X_train_resampled.iloc[train_index]
    X_val_fold = X_train_resampled.iloc[val_index]
    y_train_fold = y_train_resampled[train_index]
    y_val_fold = y_train_resampled[val_index]

    # Define Neural Network Model
    nn_model = Sequential([
        Dense(32, input_dim=X_train_fold.shape[1], activation='relu', kernel_regularizer=l2(0.01)),
        Dropout(0.7),  # Increased dropout to reduce overfitting
        Dense(16, activation='relu', kernel_regularizer=l2(0.01)),
        Dense(3, activation='softmax')  # Assuming 3 classes
    ])

    # Compile the model
    nn_model.compile(optimizer='adam', loss='sparse_categorical_crossentropy', metrics=['accuracy'])

    # Early Stopping and Learning Rate Scheduler
    early_stopping = EarlyStopping(monitor='val_loss', patience=10, restore_best_weights=True)
    reduce_lr = ReduceLROnPlateau(monitor='val_loss', factor=0.1, patience=3, min_lr=1e-6)

    # Train the model
    history = nn_model.fit(
        X_train_fold, y_train_fold,
        epochs=50, batch_size=32,
        validation_data=(X_val_fold, y_val_fold),
        callbacks=[early_stopping, reduce_lr],
        verbose=0
    )

    # Save the training and validation losses for each fold
    train_losses.append(history.history['loss'])
    validation_losses.append(history.history['val_loss'])
    accuracies.append(history.history['accuracy'][-1])

    # Update max_epochs
    max_epochs = max(max_epochs, len(history.history['loss']))

    # Track the best model based on validation loss
    val_loss = min(history.history['val_loss'])
    if val_loss < best_val_loss:
        best_val_loss = val_loss
        best_model = nn_model

# Pad the loss arrays to have the same length (max_epochs)
def pad_loss_array(losses, max_length):
    return [loss + [loss[-1]] * (max_length - len(loss)) for loss in losses]

train_losses = np.array(pad_loss_array(train_losses, max_epochs))
validation_losses = np.array(pad_loss_array(validation_losses, max_epochs))

# Calculate mean and standard deviation of losses and accuracy
mean_train_loss = train_losses.mean(axis=0)
mean_val_loss = validation_losses.mean(axis=0)
mean_accuracy = np.mean(accuracies)

# Plot the loss curves for all folds
plt.figure(figsize=(10, 6))
plt.plot(mean_train_loss, label='Mean Train Loss')
plt.plot(mean_val_loss, label='Mean Validation Loss')
plt.title('Train vs Validation Loss (Cross-Validation)', fontsize=14)
plt.xlabel('Epochs', fontsize=12)
plt.ylabel('Loss', fontsize=12)
plt.legend(fontsize=12)
plt.grid(True)
plt.show()

# Check if the model is overfitting by comparing the training and validation loss
overfitting_gap = mean_val_loss[-1] - mean_train_loss[-1]
if overfitting_gap > 0.1:  # Define a threshold for overfitting
    print("Model might be overfitting! Consider tuning hyperparameters or adding regularization.")
else:
    print("Model is not overfitting.")

# Print the average accuracy across all folds
print(f'Average Accuracy: {mean_accuracy:.4f}')

# Use the best model (based on validation performance) to make predictions on the test set
y_pred_nn = np.argmax(best_model.predict(X_test), axis=1)

# Evaluate the model on the test set
print("\nNeural Network Evaluation on Test Set:")
print(classification_report(y_test, y_pred_nn))

# Confusion Matrix
cm = confusion_matrix(y_test, y_pred_nn)
sns.heatmap(cm, annot=True, fmt='d', cmap='Reds', cbar=False)
plt.title("Confusion Matrix", fontsize=14)
plt.xlabel("Predicted", fontsize=12)
plt.ylabel("Actual", fontsize=12)
plt.show()



from sklearn.metrics import roc_curve, auc
from sklearn.preprocessing import label_binarize
import matplotlib.pyplot as plt

# Assuming y_test contains integer class labels
n_classes = len(np.unique(y_test))  # Number of classes
y_test_binarized = label_binarize(y_test, classes=np.arange(n_classes))

# Obtain predicted probabilities for each class
y_prob = best_model.predict(X_test)

# Compute ROC curve and AUC for each class
fpr = dict()
tpr = dict()
roc_auc = dict()

for i in range(n_classes):
    fpr[i], tpr[i], _ = roc_curve(y_test_binarized[:, i], y_prob[:, i])
    roc_auc[i] = auc(fpr[i], tpr[i])

# Compute micro-average ROC curve and ROC area
fpr["micro"], tpr["micro"], _ = roc_curve(y_test_binarized.ravel(), y_prob.ravel())
roc_auc["micro"] = auc(fpr["micro"], tpr["micro"])

# Plot ROC curve for each class
plt.figure(figsize=(10, 8))
for i in range(n_classes):
    plt.plot(fpr[i], tpr[i], label=f'Class {i}')

# Plot micro-average ROC curve
plt.plot(fpr["micro"], tpr["micro"],
         label=f'Micro-average (AUC = {roc_auc["micro"]:.2f})',
         linestyle=':', linewidth=4)

# Plot random chance line
plt.plot([0, 1], [0, 1], 'k--', linewidth=2)

plt.title('ROC Curve for Neural Network')
plt.xlabel('False Positive Rate (FPR)')
plt.ylabel('True Positive Rate (TPR)')
plt.legend(loc="lower right")
plt.grid(alpha=0.5)
plt.show()

#best_model.save('best_nn_model.keras') 



#CONCLUSION
# We chose a neural network for this task because it is highly effective in capturing complex patterns and non-linear relationships in data,
# which can significantly improve classification accuracy. Overfitting was initially a concern as neural networks tend to memorize the
# training data if not properly regularized, but we addressed this by using techniques like dropout and L2 regularization to prevent 
# the model from fitting too closely to the training data. Additionally, we applied SMOTE (Synthetic Minority Over-sampling Technique) 
# to handle class imbalance by generating synthetic data for the underrepresented class, ensuring the model was trained on a
# more balanced dataset and improving its generalization ability.


#BUSINESS OBJECTIVE
"To develop an accurate and reliable machine learning model that predicts the likelihood of heart disease in individuals based on various health metrics. The goal is to empower healthcare professionals with a data-driven tool that aids in early diagnosis and personalized treatment plans, ultimately improving patient outcomes and reducing healthcare costs. By leveraging advanced techniques such as neural networks, SMOTE for handling imbalanced data, and overfitting prevention strategies, the model ensures robust and generalized predictions, making it suitable for real-world healthcare applications."

