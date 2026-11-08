FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt pyproject.toml ./
RUN pip install --no-cache-dir -r requirements.txt
COPY vaneck ./vaneck
COPY data ./data
COPY docs ./docs
COPY tests ./tests
RUN pip install --no-cache-dir -e . && python -m pytest -q --deselect tests/test_golden.py
ENTRYPOINT ["vaneck"]
CMD ["check"]
