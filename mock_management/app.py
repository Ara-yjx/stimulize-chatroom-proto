from flask import Flask
from flask_cors import CORS

from routes import register_routes


def create_app():
    app = Flask(__name__)
    CORS(app)
    register_routes(app)
    return app


app = create_app()

# Lambda adapter via Mangum (used when deployed to AWS Lambda)
try:
    from mangum import Mangum

    lambda_handler = Mangum(app)
except ImportError:
    # mangum not installed — running locally only
    pass

if __name__ == "__main__":
    app.run(debug=True, port=5001)
