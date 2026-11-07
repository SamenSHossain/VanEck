.PHONY: test lint check report replay rebalance monitor

test:
	python -m pytest -q

lint:
	ruff check vaneck tests

check:
	python -m vaneck check

report:
	python -m vaneck all --force

replay:
	python -m vaneck replay --strategy EqualWeight --start 2024-01-01 --end 2024-12-31

rebalance:
	python -m vaneck rebalance --strategy EqualWeight --init-cash 10000000

monitor:
	python -m vaneck monitor --strategy EqualWeight --mark
