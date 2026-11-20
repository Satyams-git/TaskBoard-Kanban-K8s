# TaskBoard: Kubernetes Capstone Project

A bright 3-tier task manager (kanban board) used to practise Kubernetes Modules 1-13 end to end.

```
Browser ──> Frontend (nginx, :8080) ──/api──> Backend (Flask, :5000) ──> MySQL (StatefulSet, :3306)
```

## Folder layout

```
taskboard/
├── app/
│   ├── backend/            Flask API + Dockerfile (multi-stage, non-root)
│   ├── frontend/           HTML/CSS/JS + nginx template + Dockerfile (non-root)
│   └── docker-compose.yaml Run everything locally with Docker
├── k8s/                    Plain YAML, applied step by step (Phases 2-3)
│   ├── 00-namespace.yaml
│   ├── 01-configmap.yaml
│   ├── 02-secret.yaml
│   ├── 03-mysql.yaml       Headless Service + StatefulSet + PVC + node affinity
│   ├── 04-backend.yaml     Deployment + ClusterIP Service, probes, anti-affinity
│   ├── 05-frontend.yaml    Deployment + NodePort Service (30080)
│   ├── 06-ingress.yaml     Traefik Ingress: / -> frontend, /api -> backend
│   ├── 07-hpa.yaml         Backend autoscaling 2-6 pods at 50% CPU
│   ├── 08-pdb.yaml         Keep 1 backend pod during drains
│   ├── 09-vpa.yaml         MySQL size recommendations (Off mode)
│   └── 10-log-agent.yaml   DaemonSet on every node
└── helm/taskboard/         The same app as one Helm chart (Phase 5-6)
    ├── values.yaml
    ├── values-dev.yaml     Kind
    └── values-prod.yaml    kubeadm on AWS
```

## Quick start

```bash
# 1. Build and push images (replace with your Docker Hub user)
export DOCKER_USER=yourname
docker build -t $DOCKER_USER/taskboard-backend:v1 app/backend
docker build -t $DOCKER_USER/taskboard-frontend:v1 app/frontend
docker push $DOCKER_USER/taskboard-backend:v1
docker push $DOCKER_USER/taskboard-frontend:v1

# 2. Put your user into the YAML files
sed -i "s/YOUR_DOCKERHUB_USER/$DOCKER_USER/g" k8s/*.yaml

# 3. Label one worker for MySQL, then deploy
kubectl label $(kubectl get nodes -o name -l '!node-role.kubernetes.io/control-plane' | head -1) role=db
kubectl apply -f k8s/00-namespace.yaml
kubectl apply -f k8s/01-configmap.yaml -f k8s/02-secret.yaml -f k8s/03-mysql.yaml -f k8s/04-backend.yaml -f k8s/05-frontend.yaml
```

Full guided steps are in the course guide document.

## API

| Method | Path | Purpose |
| --- | --- | --- |
| GET | /api/health | Liveness probe |
| GET | /api/ready | Readiness probe (checks MySQL) |
| GET | /api/info | Pod, node, version, environment |
| GET / POST | /api/tasks | List / create tasks |
| PUT / DELETE | /api/tasks/{id} | Update / delete a task |
| GET | /api/stress?seconds=N | Burn CPU (HPA demo, max 30 s) |
