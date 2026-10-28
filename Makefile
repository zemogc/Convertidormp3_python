.PHONY: up down logs test lint format e2e k8s-images k8s-apply

PY ?= python

up:            ## Levanta todo el sistema en http://localhost:8080
	docker compose up -d --build

down:          ## Detiene el sistema (conserva los datos)
	docker compose down

logs:          ## Sigue los logs de los servicios de la app
	docker compose logs -f auth gateway converter notification

test:          ## Tests unitarios (sin Docker)
	$(PY) -m pytest

lint:          ## Revisa estilo
	ruff check src scripts
	ruff format --check src scripts

format:
	ruff check --fix src scripts
	ruff format src scripts

e2e: up       ## Prueba de punta a punta contra el stack de compose
	$(PY) scripts/e2e.py

k8s-images:    ## Construye las imagenes dentro de minikube
	for svc in auth gateway converter notification; do \
		minikube image build -t convertidor/$$svc:latest src/$$svc; \
	done

k8s-apply:     ## Aplica todos los manifiestos
	kubectl apply -f src/rabbit/manifest
	kubectl apply -f src/auth/manifests
	kubectl apply -f src/gateway/manifests
	kubectl apply -f src/converter/manifests
	kubectl apply -f src/notification/manifests
