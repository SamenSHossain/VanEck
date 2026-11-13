.PHONY: test lint typecheck check cross report extensions golden replay rebalance monitor status history dashboard docker webdash

test:
	python -m pytest -q

lint:
	ruff check vaneck tests

check:
	python -m vaneck check

report:
	python -m vaneck all --force

# replay, rebalance and monitor never touch the committed 2024 paper book past 2024-12-31: the 2025+ hold-out is
# sealed (docs/SPEC.md section 19), and a decision on that book would mark it through the sealed period.
# replay keeps the decision records already in its folder, and on a second run every month would come out duplicate
# with nothing bought, so the folder is cleared first.
replay:
	rm -rf /tmp/vaneck-replay && python -m vaneck replay --strategy EqualWeight --start 2024-01-01 --end 2024-12-31 \
	  --book /tmp/vaneck-replay/book.json --decisions /tmp/vaneck-replay/decisions --out /tmp/vaneck-replay

rebalance:
	python -m vaneck rebalance --strategy EqualWeight --init-cash 10000000 \
	  --book /tmp/vaneck-dryrun/book.json --decisions /tmp/vaneck-dryrun/decisions

monitor:
	python -m vaneck monitor --strategy EqualWeight

typecheck:
	python -m mypy

cross:
	python -m vaneck fetch --secondary && python -m vaneck check --cross

extensions:
	python -m vaneck extensions

golden:
	python -m vaneck golden

status:
	python -m vaneck status

history:
	python -m vaneck history --last 30

dashboard:
	python -m vaneck dashboard

webdash:
	python -m pytest -q --junitxml=reports/junit.xml && python -m vaneck webdash

docker:
	docker build -t vaneck . && docker run --rm vaneck check --now data
