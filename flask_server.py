from flask import Flask
app = Flask(__name__)


@app.route('/hello')
def hello_world():

    return 'Hello World!'


# gunicorn -w 8 -b 0.0.0.0:8000 flask_server:app
if __name__ == '__main__':
    app.run(host="0.0.0.0", port=8080, debug=False)
