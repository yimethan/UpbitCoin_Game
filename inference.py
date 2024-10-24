import tensorflow as tf
import os
import joblib
import pandas as pd
import numpy as np
import time
import pyupbit
import datetime
import requests
import json
import warnings
from threading import Thread

import config

warnings.filterwarnings('ignore')


model_dir = 'ai_hint/models'
data_dir = 'data/from_pyupbit'

coins = ['KRW-BTC', 'KRW-SXP', 'KRW-SUI', 'KRW-ARK', 'KRW-SHIB', 'KRW-SEI', 'KRW-HIFI', 'KRW-XRP', 'KRW-UXLINK']
coin_dict = {coin: i for i, coin in enumerate(coins)}

cfg = config.Config()

last_times = {}  # csv 파일 마지막에 빈 줄 하나 있어야 함

models = []
scalers = []
data_paths = {}
model_paths = {}
scaler_paths = {}

response = []


def get_last_date(coin):
    filepath = os.path.join(data_dir, f'{coin}.csv')
    total_rows = sum(1 for _ in open(filepath))
    last_row = pd.read_csv(filepath, skiprows=range(1, total_rows-1))
    last_date = last_row['timestamp'].values[0]
    return last_date


def get_last_row(coin, count=10):
    filepath = os.path.join(data_dir, f'{coin}.csv')
    total_rows = sum(1 for _ in open(filepath))
    last_row = pd.read_csv(filepath, skiprows=range(1, total_rows-count))
    return last_row

for coin in coins:
    model_paths[coin] = os.path.join(model_dir, f'lstm_{coin}.h5')
    models.append(tf.keras.models.load_model(model_paths[coin]))
    
    scaler_paths[coin] = os.path.join(model_dir, f'{coin}_scaler.pkl')
    scalers.append(joblib.load(scaler_paths[coin]))
    
    data_paths[coin] = os.path.join(data_dir, f'{coin}.csv')
    
    last_times[coin] = get_last_date(coin)
    
    response.append({'code':coin, 'rank':-1, 'prediction_timestamp':'', 'percentage':0.0,
                     'most_volatile':False, 'least_volatile':False, 'largest_drop':False, 'largest_rise':False,
                     'largest_spike':False, 'fastest_growth':False, 'fastest_decline':False})
    
    
# TODO: get data from DB
def get_data(coin, count=1, to=datetime.datetime.now().strftime('%Y-%m-%d %H:%M:00')):
    return pyupbit.get_ohlcv(ticker=coin, interval='minute1', count=count, to=to)


def get_percentage(future, current):
    return (future - current) / current * 100


def before_train():
    start_time = datetime.datetime.now()
    print('Before train', start_time)
    now = datetime.datetime.now()
    current_time = now.strftime('%Y-%m-%d %H:%M:00')
    now = datetime.datetime.strptime(current_time, '%Y-%m-%d %H:%M:%S')
    while any(last_times[coin][:-2]+'00' < current_time for coin in last_times.keys()):
        for i, coin in enumerate(coins):
            if current_time > last_times[coin]:
                diff_min = now - datetime.datetime.strptime(last_times[coin], '%Y-%m-%d %H:%M:%S')
                diff_min = diff_min.total_seconds() // 60
                this_time = get_data(coin=coin, count=int(diff_min), to=current_time).reset_index()
                this_time.to_csv(data_paths[coin], mode='a', header=not os.path.exists(data_paths[coin]), index=False)
                    
                X = this_time['close'].values.reshape(-1, 1)
                scalers[i].partial_fit(X)
                joblib.dump(scalers[i], scaler_paths[coin])
                scaled_data = scalers[i].transform(X)
                
                X = []
                y = []
                for j in range(len(scaled_data) - 1):
                    X.append(scaled_data[j:(j + 1), 0])
                    y.append(scaled_data[j + 1, 0])
                print(i, coin, len(X), len(y))
                    
                if len(X) >= 1 and len(y) >= 1:
                    X, y = np.array(X), np.array(y)
                    X = X.reshape(X.shape[0], X.shape[1], 1)
                    models[i].fit(X, y, epochs=1, batch_size=1)
                    models[i].save(model_paths[coin])
                
                last_times[coin] = current_time
                now = datetime.datetime.now()
                current_time = str(now).split('.')[0][:-2] + '00'
            print(f'{coin} loop', datetime.datetime.now() - start_time)
            start_time = datetime.datetime.now()
            
            
def on_message(msg):
    url = cfg.url
    headers = {'Content-Type': 'application/json'}
    
    try:
        r = requests.post(url, data=msg, headers=headers)
        print(f'Status Code: {r.status_code}, Response: {r.text}')
    except requests.exceptions.RequestException as e:
        print(f'An error occurred: {e}')
    
    return response


volatility = {}
price_change ={}
volume_change = {}
avg_change_rate = {}


def analyze_and_predict():
    
    percentages = {}

    while True:
        for i, coin in enumerate(coins):
            
            # TODO: analyze
            
            recent_rows = get_last_row(coin, cfg.rows)
            recent_closes = recent_rows['close'].values
            
            percentage_changes = [get_percentage(recent_closes[i+1], recent_closes[i]) for i in range(len(recent_closes) - 1)]
            volatility[coin] = np.std(percentage_changes)  # 5분 간의 변동성
            price_change[coin] = get_percentage(recent_closes[-1], recent_closes[0])  # 5분 간의 가격 변화율
            volume_change[coin] = get_percentage(recent_rows['volume'].iloc[-1], recent_rows['volume'].iloc[0])  # 5분 간의 거래량 변화
            avg_change_rate[coin] = sum(percentage_changes) / len(percentage_changes)  # 5분 간의 평균 변화율
            
            # TODO: predict
            
            curr_price = recent_closes[-1]
            X = scalers[i].transform(curr_price.reshape(-1, 1))
            X = X.reshape(X.shape[0], X.shape[1], 1)
            
            pred = models[i].predict(X)
            pred = scalers[i].inverse_transform(pred)
            
            percentages[coin] = get_percentage(pred[0][0], curr_price)
            
        sorted_percentages = sorted(percentages.items(), key=lambda x: x[1], reverse=True)
        
        pred_time = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        for i, (coin, percentage) in enumerate(sorted_percentages):
            response[coin_dict[coin]]['percentage'] = percentage
            response[coin_dict[coin]]['rank'] = i + 1
            response[coin_dict[coin]]['prediction_timestamp'] = pred_time
        
        volatile_coin_idx = coin_dict[max(volatility, key=volatility.get)]
        response[volatile_coin_idx]['most_volatile'] = True
        not_volatile_coin_idx = coin_dict[min(volatility, key=volatility.get)]
        if not_volatile_coin_idx == volatile_coin_idx:
            response[coin_dict[sorted(volatility.items(), key=lambda x: x[1])[1][0]]]['least_volatile'] = True
        else:
            response[not_volatile_coin_idx]['least_volatile'] = True
            
        largest_rise_coin = coin_dict[max(price_change, key=price_change.get)]
        response[largest_rise_coin]['largest_rise'] = True
        largest_drop_coin = coin_dict[min(price_change, key=price_change.get)]
        if largest_rise_coin == largest_drop_coin:
            response[coin_dict[sorted(price_change.items(), key=lambda x: x[1])[1][0]]]['largest_drop'] = True
        else:
            response[largest_drop_coin]['largest_drop'] = True
        
        response[coin_dict[max(volume_change, key=volume_change.get)]]['largest_spike'] = True
        
        fastest_growth_coin = coin_dict[max(avg_change_rate, key=avg_change_rate.get)]
        response[fastest_growth_coin]['fastest_growth'] = True
        fastest_decline_coin = coin_dict[min(avg_change_rate, key=avg_change_rate.get)]
        if fastest_growth_coin == fastest_decline_coin:
            response[coin_dict[sorted(avg_change_rate.items(), key=lambda x: x[1])[1][0]]]['fastest_decline'] = True
        else:
            response[fastest_decline_coin]['fastest_decline'] = True
        
        print(response)
        
        # TODO: send
        
        message = json.dumps(response)
        on_message(message)
        
        print('Trained and analyzed all coins', datetime.datetime.now())
        time.sleep(cfg.seconds)
        
        response[coin_dict[max(volatility, key=volatility.get)]]['most_volatile'] = False
        response[coin_dict[min(volatility, key=volatility.get)]]['least_volatile'] = False
        response[coin_dict[min(price_change, key=price_change.get)]]['largest_drop'] = False
        response[coin_dict[max(price_change, key=price_change.get)]]['largest_rise'] = False
        response[coin_dict[max(volume_change, key=volume_change.get)]]['largest_spike'] = False
        response[coin_dict[max(avg_change_rate, key=avg_change_rate.get)]]['fastest_growth'] = False
        response[coin_dict[min(avg_change_rate, key=avg_change_rate.get)]]['fastest_decline'] = False


def train():
    
    while True:
    
        for i, coin in enumerate(coins):

            now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:00')
            if last_times[coin] >= now:
                continue
            
            diff_min = datetime.datetime.strptime(now, '%Y-%m-%d %H:%M:%S') - datetime.datetime.strptime(last_times[coin], '%Y-%m-%d %H:%M:%S')
            diff_min = diff_min.total_seconds() // 60
            this_time = get_data(coin=coin, count=int(diff_min), to=now).reset_index()
            this_time.to_csv(data_paths[coin], mode='a', header=not os.path.exists(data_paths[coin]), index=False)
            
            last_times[coin] = now
            
            # TODO: train
            
            if len(this_time) == 0:
                continue
            
            X = this_time.close.values.reshape(-1, 1)
            scalers[i].partial_fit(X)
            joblib.dump(scalers[i], scaler_paths[coin])
            scaled_data = scalers[i].transform(X)
            
            X, y = [], []
            
            for i in range(len(scaled_data) - 1):
                X.append(scaled_data[i:(i + 1), 0])
                y.append(scaled_data[i + 1, 0])
            
            X, y = np.array(X), np.array(y)
            X = X.reshape(X.shape[0], X.shape[1], 1)
            
            models[i].fit(X, y, epochs=1, batch_size=1)
            models[i].save(model_paths[coin])
            
            print(f'{coin} trained', last_times[coin])

    
if __name__ == '__main__':
    before_train()
    Thread(target=train).start()
    Thread(target=analyze_and_predict).start()
    