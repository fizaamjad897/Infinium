#Importing all libraries 
import pandas as pd

#Importing the dataset
file_path = r'F:\5th Semester\AI\Theory\AI Project\Model Multi Class\heart.csv'
heart_df = pd.read_csv(file_path)


#Examining the raw data
print(heart_df.head())

print(heart_df.info())

#Add new features to the raw dataset

additional_file_path = r'F:\5th Semester\AI\Theory\AI Project\Model Multi Class\FIC.FULL CSV.csv'
additional_data = pd.read_csv(additional_file_path)

# Display the structure and first few rows of the additional file to understand its data
additional_data.info(), additional_data.head()


def update_features(main_df, reference_df):
    # Standardize column names for easier matching
    reference_df.rename(columns={
        'Family.History': 'FamilyHistory',
        'Smoking': 'Smoking',
        'Diabetes': 'Diabetes'
    }, inplace=True)

    # Initialize new columns in the main dataset
    main_df['Smoking'] = 'NO'
    main_df['Diabetes'] = 0
    main_df['FamilyHistory'] = 'NO'

    for index, row in main_df.iterrows():
        matched_row = reference_df[
            (reference_df['trestbps'] == row['RestingBP']) & 
            (reference_df['chol'] == row['Cholesterol']) 
        ]
        if not matched_row.empty:
            # Update the new features with values from the reference dataset
            main_df.at[index, 'Smoking'] = matched_row.iloc[0]['Smoking']
            main_df.at[index, 'Diabetes'] = matched_row.iloc[0]['Diabetes']
            main_df.at[index, 'FamilyHistory'] = matched_row.iloc[0]['FamilyHistory']

    return main_df


updated_heart_df = update_features(heart_df, additional_data)

updated_file_path = r'F:\5th Semester\AI\Theory\AI Project\Model Multi Class\Final Heart Dataset.csv'
updated_heart_df.to_csv(updated_file_path, index=False)
print(f"Updated dataset saved to: {updated_file_path}")

