'''
Goal of LSTM microservice:
1. LSTM microservice will accept the GitHub data from Flask microservice and will forecast the data for next 1 year based on past 30 days
2. It will also plot three different graph (i.e.  "Model Loss", "LSTM Generated Data", "All Issues Data") using matplot lib 
3. This graph will be stored as image in Google Cloud Storage.
4. The image URL are then returned back to Flask microservice.
'''
# Import all the required packages
from flask import Flask, jsonify, request, make_response, Response
import os
from dateutil import *
import dateutil.relativedelta
from datetime import date
from datetime import timedelta
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import time
from flask_cors import CORS
import requests

# Tensorflow (Keras & LSTM) related packages
import tensorflow as tf
from tensorflow.python.keras import Sequential
from tensorflow.python.keras.layers import Input, Dense, LSTM, Dropout
from tensorflow.python.keras.callbacks import EarlyStopping
from sklearn.preprocessing import MinMaxScaler
from keras.preprocessing.sequence import TimeseriesGenerator
import json

# Import required storage package from Google Cloud Storage
from google.cloud import storage

    

# Initilize flask app


app = Flask(__name__)
# Handles CORS (cross-origin resource sharing)
CORS(app)
# Initlize Google cloud storage client

def authenticate_implicit_with_adc(project_id="steel-ace-369218"):
        client = storage.Client(project= project_id)
# Add response headers to accept all types of  requests

def build_preflight_response():
    response = make_response()
    response.headers.add("Access-Control-Allow-Origin", "*")
    response.headers.add("Access-Control-Allow-Headers", "Content-Type")
    response.headers.add("Access-Control-Allow-Methods",
                         "PUT, GET, POST, DELETE, OPTIONS")
    return response

#  Modify response headers when returning to the origin

def build_actual_response(response):
    response.headers.set("Access-Control-Allow-Origin", "*")
    response.headers.set("Access-Control-Allow-Methods",
                         "PUT, GET, POST, DELETE, OPTIONS")
    return response

'''
API route path is  "/api/forecast"
This API will accept only POST request
'''

@app.route('/api/forecast', methods=['POST'])
def forecast():
    body = request.get_json()
    issues = body["issues"]
    type = body["type"]
    repo_name = body["repo"]
    data_frame = pd.DataFrame(issues)
    df1 = data_frame.groupby([type], as_index=False).count()
    df = df1[[type, 'issue_number']]
    df.columns = ['ds', 'y']

    df['ds'] = df['ds'].astype('datetime64[ns]')
    array = df.to_numpy()
    x = np.array([time.mktime(i[0].timetuple()) for i in array])
    y = np.array([i[1] for i in array])

    lzip = lambda *x: list(zip(*x))

    days = df.groupby('ds')['ds'].value_counts()
    Y = df['y'].values
    X = lzip(*days.index.values)[0]
    firstDay = min(X)

    '''
    To achieve data consistancy with both actual data and predicted values, 
    add zeros to dates that do not have orders
    [firstDay + timedelta(days=day) for day in range((max(X) - firstDay).days + 1)]
    '''
    Ys = [0, ]*((max(X) - firstDay).days + 1)
    days = pd.Series([firstDay + timedelta(days=i)
                      for i in range(len(Ys))])
    for x, y in zip(X, Y):
        Ys[(x - firstDay).days] = y

    # Modify the data that is suitable for LSTM
    Ys = np.array(Ys)
    Ys = Ys.astype('float32')
    Ys = np.reshape(Ys, (-1, 1))
    # Apply min max scaler to transform the data
    scaler = MinMaxScaler(feature_range=(0, 1))
    Ys = scaler.fit_transform(Ys)
    # Divide training - test data with 80-20 split
    train_size = int(len(Ys) * 0.80)
    test_size = len(Ys) - train_size
    train, test = Ys[0:train_size, :], Ys[train_size:len(Ys), :]
    print('train size:', len(train), ", test size:", len(test))

    # Create the training and test dataset
    def create_dataset(dataset, look_back=1):
        X, Y = [], []
        for i in range(len(dataset)-look_back-1):
            a = dataset[i:(i+look_back), 0]
            X.append(a)
            Y.append(dataset[i + look_back, 0])
        return np.array(X), np.array(Y)
    '''
    Look back decides how many days of data the model looks at for prediction
    Here LSTM looks at approximately one month data
    '''
    look_back = 30
    X_train, Y_train = create_dataset(train, look_back)
    X_test, Y_test = create_dataset(test, look_back)

    # Reshape input to be [samples, time steps, features]
    X_train = np.reshape(X_train, (X_train.shape[0], 1, X_train.shape[1]))
    X_test = np.reshape(X_test, (X_test.shape[0], 1, X_test.shape[1]))

    # Verifying the shapes
    X_train.shape, X_test.shape, Y_train.shape, Y_test.shape

    # Model to forecast
    model = Sequential()
    model.add(LSTM(100, input_shape=(X_train.shape[1], X_train.shape[2])))
    model.add(Dropout(0.2))
    model.add(Dense(1))
    model.compile(loss='mean_squared_error', optimizer='adam')

    # Fit the model with training data and set appropriate hyper parameters
    history = model.fit(X_train, Y_train, epochs=20, batch_size=70, validation_data=(X_test, Y_test),
                        callbacks=[EarlyStopping(monitor='val_loss', patience=10)], verbose=1, shuffle=False)

    '''
    Creating image URL
    BASE_IMAGE_PATH refers to Google Cloud Storage Bucket URL.Add your Base Image Path in line 145
    if you want to run the application local
    LOCAL_IMAGE_PATH refers local directory where the figures generated by matplotlib are stored
    These locally stored images will then be uploaded to Google Cloud Storage
    '''
    BASE_IMAGE_PATH = os.environ.get(
        'BASE_IMAGE_PATH', 'Your_Base_Image_path')
    # DO NOT DELETE "static/images" FOLDER as it is used to store figures/images generated by matplotlib
    LOCAL_IMAGE_PATH = "static/images/"

    # Creating the image path for model loss, LSTM generated image and all issues data image
    MODEL_LOSS_IMAGE_NAME = "model_loss_" + type +"_"+ repo_name + ".png"
    MODEL_LOSS_URL = BASE_IMAGE_PATH + MODEL_LOSS_IMAGE_NAME

    LSTM_GENERATED_IMAGE_NAME = "lstm_generated_data_" + type +"_" + repo_name + ".png"
    LSTM_GENERATED_URL = BASE_IMAGE_PATH + LSTM_GENERATED_IMAGE_NAME

    ALL_ISSUES_DATA_IMAGE_NAME = "all_issues_data_" + type + "_"+ repo_name + ".png"
    ALL_ISSUES_DATA_URL = BASE_IMAGE_PATH + ALL_ISSUES_DATA_IMAGE_NAME
    
    STACKED_BAR_CHART = "stacked_bar_chart" + type + "_"+ repo_name + ".png"
    STACKED_BAR_CHART_URL = BASE_IMAGE_PATH + STACKED_BAR_CHART
    
    WEEK_LINE_CHART = "week_line_chart" + type + "_"+ repo_name + ".png"
    WEEK_LINE_CHART_URL = BASE_IMAGE_PATH + WEEK_LINE_CHART
    
    WEEK_LINE_CHART_CLOSED = "week_line_chart_closed" + type + "_"+ repo_name + ".png"
    WEEK_LINE_CHART_CLOSED_URL = BASE_IMAGE_PATH + WEEK_LINE_CHART_CLOSED
    
    MONTH_LINE_CHART_CLOSED = "month_line_chart_closed" + type + "_"+ repo_name + ".png"
    MONTH_LINE_CHART_CLOSED_URL = BASE_IMAGE_PATH + MONTH_LINE_CHART_CLOSED

    PULL_CHART = "pull_chart_"+ repo_name + ".png"
    PULL_CHART_URL = BASE_IMAGE_PATH + PULL_CHART
    
    PULL_CHART_LOSS = "pull_chart_loss_"+ repo_name + ".png"
    PULL_CHART_LOSS_URL = BASE_IMAGE_PATH + PULL_CHART_LOSS
    
    PULL_CHART_PREDICTIONS = "pull_chart_predictions_"+ repo_name + ".png"
    PULL_CHART_PREDICTIONS_URL = BASE_IMAGE_PATH + PULL_CHART_PREDICTIONS

    # Add your unique Bucket Name if you want to run it local
    BUCKET_NAME = os.environ.get(
        'BUCKET_NAME', 'Your_BUCKET_NAME')

    # Model summary()

    # Plot the model loss image
    plt.figure(figsize=(8, 4))
    plt.plot(history.history['loss'], label='Train Loss')
    plt.plot(history.history['val_loss'], label='Test Loss')
    plt.title('Model Loss For ' + type)
    plt.ylabel('Loss')
    plt.xlabel('Epochs')
    plt.legend(loc='upper right')
    # Save the figure in /static/images folder
    plt.savefig(LOCAL_IMAGE_PATH + MODEL_LOSS_IMAGE_NAME)

    # Predict issues for test data
    y_pred = model.predict(X_test)

    # Plot the LSTM Generated image
    fig, axs = plt.subplots(1, 1, figsize=(10, 4))
    X = mdates.date2num(days)
    axs.plot(np.arange(0, len(Y_train)), Y_train, 'g', label="history")
    axs.plot(np.arange(len(Y_train), len(Y_train) + len(Y_test)),
             Y_test, marker='.', label="true")
    axs.plot(np.arange(len(Y_train), len(Y_train) + len(Y_test)),
             y_pred, 'r', label="prediction")
    axs.legend()
    axs.set_title('LSTM Generated Data For ' + type)
    axs.set_xlabel('Time Steps')
    axs.set_ylabel('Issues')
    # Save the figure in /static/images folder
    plt.savefig(LOCAL_IMAGE_PATH + LSTM_GENERATED_IMAGE_NAME)

    # Plot the All Issues data images
    fig, axs = plt.subplots(1, 1, figsize=(10, 4))
    X = mdates.date2num(days)
    axs.plot(X, Ys, 'purple', marker='.')
    locator = mdates.AutoDateLocator()
    axs.xaxis.set_major_locator(locator)
    axs.xaxis.set_major_formatter(mdates.AutoDateFormatter(locator))
    axs.legend()
    axs.set_title('All Issues Data')
    axs.set_xlabel('Date')
    axs.set_ylabel('Issues')
    # Save the figure in /static/images folder
    plt.savefig(LOCAL_IMAGE_PATH + ALL_ISSUES_DATA_IMAGE_NAME)
    
    
    created_at_issues = []
    closed_at_issues = []
    if not data_frame.empty:
        created_at = data_frame['created_at']
        month_issue_created = pd.to_datetime(
            pd.Series(created_at), format='%Y/%m/%d')
        month_issue_created.index = month_issue_created.dt.to_period('m')
        month_issue_created = month_issue_created.groupby(level=0).size()
        month_issue_created = month_issue_created.reindex(pd.period_range(
            month_issue_created.index.min(), month_issue_created.index.max(), freq='m'), fill_value=0)
        month_issue_created_dict = month_issue_created.to_dict()
        for key in month_issue_created_dict.keys():
            array = [str(key), month_issue_created_dict[key]]
            created_at_issues.append(array)

        closed_at = data_frame['closed_at'].sort_values(ascending=True)
        month_issue_closed = pd.to_datetime(
            pd.Series(closed_at), format='%Y/%m/%d')
        month_issue_closed.index = month_issue_closed.dt.to_period('m')
        month_issue_closed = month_issue_closed.groupby(level=0).size()
        month_issue_closed = month_issue_closed.reindex(pd.period_range(
            month_issue_closed.index.min(), month_issue_closed.index.max(), freq='m'), fill_value=0)
        month_issue_closed_dict = month_issue_closed.to_dict()
        for key in month_issue_closed_dict.keys():
            array = [str(key), month_issue_closed_dict[key]]
            closed_at_issues.append(array)

    plt.figure(figsize=(12, 7))
    x = []
    arr_y1 = []
    for i in range(len(created_at_issues)):
        x.append(created_at_issues[i][0])
        arr_y1.append(created_at_issues[i][1])
    arr_y2 = []
    for i in range(len(closed_at_issues)):
        arr_y2.append(closed_at_issues[i][1])
    plt.bar(x, arr_y1, color = 'blue')
    plt.bar(x, arr_y2, bottom = arr_y1, color='yellow')
    plt.legend(["Created Issues", "Closed Issues"])
    plt.xticks(rotation=90)
    plt.title('Stacked bar chart for to plot the created and closed issues for every Repository')
    plt.savefig(LOCAL_IMAGE_PATH + STACKED_BAR_CHART)

    x = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
    data_frame['created_at'] = pd.to_datetime(data_frame['created_at'], errors='coerce')
    week_df = data_frame.groupby(data_frame['created_at'].dt.day_name()).size()
    week_df = pd.DataFrame({'Created_On':week_df.index, 'Count':week_df.values})
    week_df = week_df.groupby(['Created_On']).sum().reindex(x)
    max_issue_count = week_df.max()
    max_issue_day = week_df['Count'].idxmax()
    plt.figure(figsize=(12, 7))
    plt.plot(week_df['Count'], label='Issues')
    plt.title('Number of Issues Created for particular Week Days.')
    plt.ylabel('Number of Issues')
    plt.xlabel('Week Days')
    plt.savefig(LOCAL_IMAGE_PATH + WEEK_LINE_CHART)
    
    data_frame['closed_at'] = pd.to_datetime(data_frame['closed_at'], errors='coerce')
    week_df = data_frame.groupby(data_frame['closed_at'].dt.day_name()).size()
    week_df = pd.DataFrame({'Closed_On':week_df.index, 'Count':week_df.values})
    week_df = week_df.groupby(['Closed_On']).sum().reindex(x)
    max_issue_count_closed = week_df.max()
    max_issue_day_closed = week_df['Count'].idxmax()
    plt.figure(figsize=(12, 7))
    plt.plot(week_df['Count'], label='Issues')
    plt.title('Number of Issues Closed for particular Week Days.')
    plt.ylabel('Number of Issues')
    plt.xlabel('Week Days')
    plt.savefig(LOCAL_IMAGE_PATH + WEEK_LINE_CHART_CLOSED)
    
    x = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December']
    data_frame['closed_at'] = pd.to_datetime(data_frame['closed_at'], errors='coerce')
    month_df = data_frame.groupby(data_frame['closed_at'].dt.month_name()).size()
    month_df = pd.DataFrame({'Closed_On':month_df.index, 'Count':month_df.values})
    month_df = month_df.groupby(['Closed_On']).sum().reindex(x)
    max_issue_count_closed_month = month_df.max()
    max_issue_closed_month = month_df['Count'].idxmax()
    plt.figure(figsize=(12, 7))
    plt.plot(month_df['Count'], label='Issues')
    plt.title('Number of Issues Closed for particular Month.')
    plt.ylabel('Number of Issues')
    plt.xlabel('Month Names')
    plt.savefig(LOCAL_IMAGE_PATH + MONTH_LINE_CHART_CLOSED)

    # Uploads an images into the google cloud storage bucket
    bucket = client.get_bucket(BUCKET_NAME)
    new_blob = bucket.blob(MODEL_LOSS_IMAGE_NAME)
    new_blob.upload_from_filename(
        filename=LOCAL_IMAGE_PATH + MODEL_LOSS_IMAGE_NAME)
    new_blob = bucket.blob(ALL_ISSUES_DATA_IMAGE_NAME)
    new_blob.upload_from_filename(
        filename=LOCAL_IMAGE_PATH + ALL_ISSUES_DATA_IMAGE_NAME)
    new_blob = bucket.blob(LSTM_GENERATED_IMAGE_NAME)
    new_blob.upload_from_filename(
        filename=LOCAL_IMAGE_PATH + LSTM_GENERATED_IMAGE_NAME)
    new_blob = bucket.blob(STACKED_BAR_CHART)
    new_blob.upload_from_filename(
        filename=LOCAL_IMAGE_PATH + STACKED_BAR_CHART)
    new_blob = bucket.blob(WEEK_LINE_CHART)
    new_blob.upload_from_filename(
        filename=LOCAL_IMAGE_PATH + WEEK_LINE_CHART)
    new_blob = bucket.blob(WEEK_LINE_CHART_CLOSED)
    new_blob.upload_from_filename(
        filename=LOCAL_IMAGE_PATH + WEEK_LINE_CHART_CLOSED)
    new_blob = bucket.blob(MONTH_LINE_CHART_CLOSED)
    new_blob.upload_from_filename(
        filename=LOCAL_IMAGE_PATH + MONTH_LINE_CHART_CLOSED)
    bucket = client.get_bucket(BUCKET_NAME)
    new_blob = bucket.blob(PULL_CHART)
    new_blob.upload_from_filename(
        filename=LOCAL_IMAGE_PATH + PULL_CHART)
    new_blob = bucket.blob(PULL_CHART_LOSS)
    new_blob.upload_from_filename(
        filename=LOCAL_IMAGE_PATH + PULL_CHART_LOSS)
    new_blob = bucket.blob(PULL_CHART_PREDICTIONS)
    new_blob.upload_from_filename(
        filename=LOCAL_IMAGE_PATH + PULL_CHART_PREDICTIONS)

    # Construct the response
    json_response = {
        "model_loss_image_url": MODEL_LOSS_URL,
        "lstm_generated_image_url": LSTM_GENERATED_URL,
        "all_issues_data_image": ALL_ISSUES_DATA_URL,
        "stacked_bar_chart": STACKED_BAR_CHART_URL,
        "week_line_chart": WEEK_LINE_CHART_URL,
        "week_line_chart1": max_issue_day,
        "week_line_chart2": str(max_issue_count),
        "week_line_chart_closed": WEEK_LINE_CHART_CLOSED_URL,
        "week_line_chart_closed1": max_issue_day_closed,
        "week_line_chart_closed2": str(max_issue_count_closed),
        "month_line_chart_closed": MONTH_LINE_CHART_CLOSED_URL,
        "month_line_chart_closed1": max_issue_closed_month,
        "month_line_chart_closed2": str(max_issue_count_closed_month),
        "pull_chart": PULL_CHART_URL,
        "pull_chart_loss": PULL_CHART_LOSS_URL,
        "pull_chart_predictions": PULL_CHART_PREDICTIONS_URL,
    }
    # Returns image url back to flask microservice
    return jsonify(json_response)

@app.route('/api/pulls', methods=['POST'])
def pulls():
    from keras.models import Sequential
    from keras.layers import Dense
    from keras.layers import LSTM

    body = request.get_json()
    data = body["pulls"]
    repo_name = body["repo"]

    BASE_IMAGE_PATH = os.environ.get(
        'BASE_IMAGE_PATH', 'Your_Base_Image_path')
    # DO NOT DELETE "static/images" FOLDER as it is used to store figures/images generated by matplotlib
    LOCAL_IMAGE_PATH = "static/images/"
    PULL_CHART = "pull_chart_"+ repo_name.split("/")[1] + ".png"
    PULL_CHART_URL = BASE_IMAGE_PATH + PULL_CHART
    
    PULL_CHART_LOSS = "pull_chart_loss_"+ repo_name.split("/")[1] + ".png"
    PULL_CHART_LOSS_URL = BASE_IMAGE_PATH + PULL_CHART_LOSS
    
    PULL_CHART_PREDICTIONS = "pull_chart_predictions_"+ repo_name.split("/")[1] + ".png"
    PULL_CHART_PREDICTIONS_URL = BASE_IMAGE_PATH + PULL_CHART_PREDICTIONS


    COMMIT_CHART = "commit_chart_"+ repo_name.split("/")[1] + ".png"
    COMMIT_CHART_URL = BASE_IMAGE_PATH + COMMIT_CHART
    
    COMMIT_CHART_LOSS = "commit_chart_loss_"+ repo_name.split("/")[1] + ".png"
    COMMIT_CHART_LOSS_URL = BASE_IMAGE_PATH + COMMIT_CHART_LOSS
    
    COMMIT_CHART_PREDICTIONS = "commit_chart_predictions_"+ repo_name.split("/")[1] + ".png"
    COMMIT_CHART_PREDICTIONS_URL = BASE_IMAGE_PATH + COMMIT_CHART_PREDICTIONS

    # Add your unique Bucket Name if you want to run it local
    BUCKET_NAME = os.environ.get(
        'BUCKET_NAME', 'Your_BUCKET_NAME')

    df = pd.DataFrame()
    arr = []
    for i in range(len(data)):
        arr.append(data[i]['created_at'])
    df['Created_At'] = arr
    df['Created_At'] = pd.to_datetime(df['Created_At'], errors='coerce')
    df['Count'] = 1
    df['Created_At'] = df['Created_At'].dt.to_period('M')
    df = df.groupby('Created_At').sum()
    
    df1 = df.copy()
    df1.index = pd.to_datetime(df1.index.to_timestamp())
    plt.figure(figsize=(12, 7))
    plt.plot(df1)
    plt.title('Number of Pulls Created for particular Month.')
    plt.ylabel('Number of Pulls')
    plt.xlabel('Time')
    plt.savefig(LOCAL_IMAGE_PATH + PULL_CHART)
    
    train_data = df[:len(df)-int(len(df)/2)]
    test_data = df[len(df)-int(len(df)/2):]
    scaler = MinMaxScaler()
    scaler.fit(train_data)
    scaled_train_data = scaler.transform(train_data)
    scaled_test_data = scaler.transform(test_data)
    n_input = int(len(df)/2)
    n_features= 1
    generator = TimeseriesGenerator(scaled_train_data, scaled_train_data, length=n_input-1, batch_size=1)
    lstm_model = Sequential()
    lstm_model.add(LSTM(200, activation='relu', input_shape=(n_input, n_features)))
    lstm_model.add(Dense(1))
    lstm_model.compile(optimizer='adam', loss='mse')
    lstm_model.fit_generator(generator,epochs=20)

    losses_lstm = lstm_model.history.history['loss']
    plt.figure(figsize=(12, 7))
    plt.xlabel("Epochs")
    plt.ylabel("Loss")
    plt.xticks(np.arange(0,21,1))
    plt.plot(range(len(losses_lstm)),losses_lstm)
    plt.savefig(LOCAL_IMAGE_PATH + PULL_CHART_LOSS)

    lstm_predictions_scaled = list()
    batch = scaled_train_data[-n_input:]
    current_batch = batch.reshape((1, n_input, n_features))
    for i in range(len(test_data)):   
        lstm_pred = lstm_model.predict(current_batch)[0]
        lstm_predictions_scaled.append(lstm_pred) 
        current_batch = np.append(current_batch[:,1:,:],[[lstm_pred]],axis=1)
    lstm_predictions = scaler.inverse_transform(lstm_predictions_scaled)
    test_data['LSTM_Predictions'] = lstm_predictions

    test_data.index = pd.to_datetime(test_data.index.to_timestamp())
    plt.figure(figsize=(12, 7))
    plt.plot(test_data['Count'])
    plt.plot(test_data['LSTM_Predictions'])
    plt.savefig(LOCAL_IMAGE_PATH + PULL_CHART_PREDICTIONS)

    bucket = client.get_bucket(BUCKET_NAME)
    new_blob = bucket.blob(PULL_CHART)
    new_blob.upload_from_filename(
        filename=LOCAL_IMAGE_PATH + PULL_CHART)
    new_blob = bucket.blob(PULL_CHART_LOSS)
    new_blob.upload_from_filename(
        filename=LOCAL_IMAGE_PATH + PULL_CHART_LOSS)
    new_blob = bucket.blob(PULL_CHART_PREDICTIONS)
    new_blob.upload_from_filename(
        filename=LOCAL_IMAGE_PATH + PULL_CHART_PREDICTIONS)
    
    new_blob = bucket.blob(COMMIT_CHART)
    new_blob.upload_from_filename(
        filename=LOCAL_IMAGE_PATH + COMMIT_CHART)
    new_blob = bucket.blob(COMMIT_CHART_LOSS)
    new_blob.upload_from_filename(
        filename=LOCAL_IMAGE_PATH + COMMIT_CHART_LOSS)
    new_blob = bucket.blob(COMMIT_CHART_PREDICTIONS)
    new_blob.upload_from_filename(
        filename=LOCAL_IMAGE_PATH + COMMIT_CHART_PREDICTIONS)
    
    json_response = {
        "pull_chart": PULL_CHART_URL,
        "pull_chart_loss": PULL_CHART_LOSS_URL,
        "pull_chart_predictions": PULL_CHART_PREDICTIONS_URL,
        "commit_chart": COMMIT_CHART_URL,
        "commit_chart_loss": COMMIT_CHART_LOSS_URL,
        "commit_chart_predictions": COMMIT_CHART_PREDICTIONS_URL,
    }
    # Returns image url back to flask microservice
    return jsonify(json_response)

@app.route('/api/commits', methods=['POST'])
def commits():
    from keras.models import Sequential
    from keras.layers import Dense
    from keras.layers import LSTM

    body = request.get_json()
    data = body["commits"]
    repo_name = body["repo"]

    BASE_IMAGE_PATH = os.environ.get(
        'BASE_IMAGE_PATH', 'Your_Base_Image_path')
    # DO NOT DELETE "static/images" FOLDER as it is used to store figures/images generated by matplotlib
    LOCAL_IMAGE_PATH = "static/images/"
    COMMIT_CHART = "commit_chart_"+ repo_name.split("/")[1] + ".png"
    COMMIT_CHART_URL = BASE_IMAGE_PATH + COMMIT_CHART
    
    COMMIT_CHART_LOSS = "commit_chart_loss_"+ repo_name.split("/")[1] + ".png"
    COMMIT_CHART_LOSS_URL = BASE_IMAGE_PATH + COMMIT_CHART_LOSS
    
    COMMIT_CHART_PREDICTIONS = "commit_chart_predictions_"+ repo_name.split("/")[1] + ".png"
    COMMIT_CHART_PREDICTIONS_URL = BASE_IMAGE_PATH + COMMIT_CHART_PREDICTIONS

    # Add your unique Bucket Name if you want to run it local
    BUCKET_NAME = os.environ.get(
        'BUCKET_NAME', 'Your_BUCKET_NAME')

    # df = pd.DataFrame(data)
    # df = df[['created_at']]
    # df.rename(columns = {'created_at':'Created_At'}, inplace = True)
    # df['Created_At'] = pd.to_datetime(df['Created_At'], errors='coerce')
    # df['Count'] = 1
    # df['Created_At'] = df['Created_At'].dt.to_period('M')
    # df = df.groupby('Created_At').sum()

    df = pd.DataFrame()
    arr = []
    for i in range(len(data)):
        arr.append(data[i]['commit']['committer']['date'])
    df['Created_At'] = arr
    df['Created_At'] = pd.to_datetime(df['Created_At'], errors='coerce')
    df['Count'] = 1
    df['Created_At'] = df['Created_At'].dt.to_period('M')
    df = df.groupby('Created_At').sum()
    
    df1 = df.copy()
    df1.index = pd.to_datetime(df1.index.to_timestamp())
    plt.figure(figsize=(12, 7))
    plt.plot(df1)
    plt.title('Number of Commits Created for particular Month.')
    plt.ylabel('Number of Commits')
    plt.xlabel('Time')
    plt.savefig(LOCAL_IMAGE_PATH + COMMIT_CHART)
    
    train_data = df[:len(df)-int(len(df)/2)]
    test_data = df[len(df)-int(len(df)/2):]
    scaler = MinMaxScaler()
    scaler.fit(train_data)
    scaled_train_data = scaler.transform(train_data)
    scaled_test_data = scaler.transform(test_data)
    n_input = int(len(df)/2)
    n_features= 1
    generator = TimeseriesGenerator(scaled_train_data, scaled_train_data, length=n_input-1, batch_size=1)
    lstm_model = Sequential()
    lstm_model.add(LSTM(200, activation='relu', input_shape=(n_input, n_features)))
    lstm_model.add(Dense(1))
    lstm_model.compile(optimizer='adam', loss='mse')
    lstm_model.fit_generator(generator,epochs=20)

    losses_lstm = lstm_model.history.history['loss']
    plt.figure(figsize=(12, 7))
    plt.xlabel("Epochs")
    plt.ylabel("Loss")
    plt.xticks(np.arange(0,21,1))
    plt.plot(range(len(losses_lstm)),losses_lstm)
    plt.savefig(LOCAL_IMAGE_PATH + COMMIT_CHART_LOSS)

    lstm_predictions_scaled = list()
    batch = scaled_train_data[-n_input:]
    current_batch = batch.reshape((1, n_input, n_features))
    for i in range(len(test_data)):   
        lstm_pred = lstm_model.predict(current_batch)[0]
        lstm_predictions_scaled.append(lstm_pred) 
        current_batch = np.append(current_batch[:,1:,:],[[lstm_pred]],axis=1)
    lstm_predictions = scaler.inverse_transform(lstm_predictions_scaled)
    test_data['LSTM_Predictions'] = lstm_predictions

    test_data.index = pd.to_datetime(test_data.index.to_timestamp())
    plt.figure(figsize=(12, 7))
    plt.plot(test_data['Count'])
    plt.plot(test_data['LSTM_Predictions'])
    plt.savefig(LOCAL_IMAGE_PATH + COMMIT_CHART_PREDICTIONS)

    bucket = client.get_bucket(BUCKET_NAME)
    new_blob = bucket.blob(COMMIT_CHART)
    new_blob.upload_from_filename(
        filename=LOCAL_IMAGE_PATH + COMMIT_CHART)
    new_blob = bucket.blob(COMMIT_CHART_LOSS)
    new_blob.upload_from_filename(
        filename=LOCAL_IMAGE_PATH + COMMIT_CHART_LOSS)
    new_blob = bucket.blob(COMMIT_CHART_PREDICTIONS)
    new_blob.upload_from_filename(
        filename=LOCAL_IMAGE_PATH + COMMIT_CHART_PREDICTIONS)
    
    json_response = {
        "commit_chart": COMMIT_CHART_URL,
        "commit_chart_loss": COMMIT_CHART_LOSS_URL,
        "commit_chart_predictions": COMMIT_CHART_PREDICTIONS_URL,
    }
    # Returns image url back to flask microservice
    return jsonify(json_response)

# Run LSTM app server on port 8080
if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=8080)
