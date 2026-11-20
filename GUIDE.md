# TaskBoard: Kubernetes Capstone Project Guide


## 1. Project overview

TaskBoard is a small team task manager (a kanban board with To Do, In Progress and Done columns). In this project you build it with Docker, run it on Kind, harden it like a production system, package it with Helm, and finally deploy it on the kubeadm cluster on AWS. Every Kubernetes topic from Modules 1 to 13 is used once, for a real reason.

### 1.1 What you will build

| Tier | Technology | Container port | Kubernetes object |
| --- | --- | --- | --- |
| Frontend | HTML, CSS, JavaScript served by nginx (non-root) | 8080 | Deployment + NodePort Service (30080) |
| Backend API | Python Flask + gunicorn (non-root) | 5000 | Deployment + ClusterIP Service |
| Database | MySQL 8.4 | 3306 | StatefulSet + Headless Service + PVC |
| Log agent | busybox | none | DaemonSet (one pod per node) |

### 1.2 How a request flows

```text
Browser
   |  http://<node-ip>:30080   (or the Ingress)
   v
Frontend Service (NodePort)  -->  Frontend pods (nginx)
                                     |  /api/...  (nginx proxy)
                                     v
                              Backend Service (ClusterIP :5000)  -->  Backend pods (Flask)
                                                                          |  SQL
                                                                          v
                                                   taskboard-mysql (Headless)  -->  taskboard-mysql-0
                                                                                         |
                                                                                         v
                                                                              PVC  -->  PV on the node labelled role=db
```

The browser only ever talks to the frontend. Nginx forwards every `/api/...` call to the backend Service, so the backend and the database are never exposed outside the cluster.

### 1.3 Where each module appears

| Module | Used in this project for |
| --- | --- |
| Docker | Two Dockerfiles (multi-stage build, non-root user, `.dockerignore`), Docker Compose test, image tags `v1` and `v2`, push to Docker Hub |
| 1. Architecture | Reading the request flow above; seeing the control plane schedule pods on workers |
| 2. Kind | Dev cluster for Phases 2 to 5 |
| 3. Namespaces, Deployments | `taskboard` namespace, backend and frontend Deployments, rolling update and rollback |
| 4. Services | ClusterIP for the backend, Headless for MySQL, NodePort for the frontend |
| 5. Ingress | Traefik Ingress: `/` to the frontend, `/api` to the backend |
| 6. ConfigMap, Secret | Database settings in a ConfigMap, passwords in a Secret |
| 7. PV, PVC | MySQL data on a PVC created from the default StorageClass |
| 8. StatefulSet | MySQL with a stable name (`taskboard-mysql-0`) and its own volume |
| 9. DaemonSet | Log agent on every node, including the master |
| 10. Scheduling | MySQL pinned to a `role=db` node; backend replicas spread across nodes; toleration for the master taint |
| 11. Autoscaling | HPA on the backend (CPU 50%), VPA recommendations for MySQL |
| 12. Helm | The whole project as one chart with dev and prod values |
| 13. kubeadm on AWS | Production install of the chart on the 3-node EC2 cluster |

### 1.4 Phases and time

| Phase | What you do | Time |
| --- | --- | --- |
| 1 | Build and push Docker images, test with Docker Compose | 60 min |
| 2 | Deploy on Kind with plain YAML, one object at a time | 90 min |
| 3 | Hardening: probes, scheduling, DaemonSet, HPA, VPA, PDB | 60 min |
| 4 | Rolling update to v2 and rollback | 30 min |
| 5 | Package as a Helm chart | 45 min |
| 6 | Production deploy on kubeadm (AWS) | 60 min |

Total is about 6 hours. It splits well into 3 sessions: Phases 1-2, Phases 3-4, Phases 5-6.

## 2. Prerequisites and project files

You need one EC2 machine with Docker and a Kind cluster (Module 2), a free Docker Hub account, and later the kubeadm cluster from Module 13.

### 2.1 Checklist

| Need | Check command | Expected |
| --- | --- | --- |
| Docker | `docker version --format '{{.Server.Version}}'` | A version number |
| Docker Compose plugin | `docker compose version` | `Docker Compose version v2...` |
| Kind cluster (1 control plane + 2 workers) | `kubectl get nodes` | 3 nodes `Ready` |
| Helm | `helm version --short` | `v3.x` or `v4.x` |
| Docker Hub account | `docker login` | `Login Succeeded` |

The Security Group of the Kind machine must allow TCP 8080 from your IP (browser access through port-forward).

### 2.2 Get the project

**Easiest: clone it from GitHub** ([Satyams-git/TaskBoard-Kanban-K8s](https://github.com/Satyams-git/TaskBoard-Kanban-K8s)). Use the same command on the Kind machine and, in Phase 6, on the kubeadm master:

```bash
sudo apt-get install -y git tree && git clone https://github.com/Satyams-git/TaskBoard-Kanban-K8s.git taskboard && cd taskboard && tree -a -I '.git'
```

No Git access? Use the zip instead:

Copy `taskboard.zip` to the machine (from your laptop), then unzip it:

```bash
scp -i <key.pem> taskboard.zip ubuntu@<PUBLIC-IP>:~
```

```bash
sudo apt-get install -y unzip tree && unzip taskboard.zip && cd taskboard && tree -a -I '.git'
```

### 2.3 What is in the project

```text
taskboard/
├── app/
│   ├── backend/              Flask API: app.py, requirements.txt, Dockerfile
│   ├── frontend/             index.html, styles.css, app.js, default.conf.template, Dockerfile
│   └── docker-compose.yaml   Run all 3 containers locally
├── k8s/                      Plain YAML files, numbered in apply order
└── helm/taskboard/           The same app as a Helm chart
```

### 2.4 The backend API

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/health` | Liveness probe: the process is alive (does not touch the DB) |
| GET | `/api/ready` | Readiness probe: MySQL answers |
| GET | `/api/info` | Which pod, node and version served the request (shown in the UI footer) |
| GET, POST | `/api/tasks` | List or create tasks |
| PUT, DELETE | `/api/tasks/<id>` | Move (change status) or delete a task |
| GET | `/api/stress?seconds=N` | Burns CPU for N seconds (max 30) to trigger the HPA |

**Why two different probes?** If liveness checked the database, a short MySQL outage would make Kubernetes restart every healthy backend pod. Readiness only stops traffic to a pod until the database is back.

### 2.5 One variable for the whole guide

Set your Docker Hub username once in every new terminal:

```bash
export DOCKER_USER=<your-dockerhub-username>
```

## 3. Phase 1: Build and push the Docker images

By the end of this phase both images are on Docker Hub, and you have seen the full app work with plain Docker before Kubernetes is involved.

### Step 1.1: Read the backend Dockerfile

```bash
cat app/backend/Dockerfile
```

| Line | What it does | Why it matters |
| --- | --- | --- |
| `FROM python:3.13-slim AS build` | Stage 1 builds Python wheels | Compilers stay in this stage only |
| `FROM python:3.13-slim` | Stage 2 is the final image | Smaller and safer image |
| `ARG APP_VERSION=v1` + `ENV APP_VERSION` | Version baked into the image | Phase 4 builds `v2` with one flag, no code change |
| `useradd --uid 10001` + `USER 10001` | Runs as a non-root user | A container break-out does not get root |
| `CMD ["gunicorn", ...]` | Production web server, 2 workers x 4 threads | Flask's built-in server is for development only |

### Step 1.2: Read the frontend Dockerfile and nginx template

```bash
cat app/frontend/Dockerfile app/frontend/default.conf.template
```

- `nginxinc/nginx-unprivileged` runs nginx as a non-root user on port **8080** (non-root users cannot open ports below 1024).
- At start-up the image fills `${BACKEND_URL}` in the template. Docker Compose uses `http://backend:5000`. Kubernetes passes the backend Service DNS name.
- `location /api/` forwards API calls to the backend. The browser never talks to the backend directly.
- `/healthz` is a tiny endpoint for the frontend probes.

### Step 1.3: Build both images

```bash
docker build -t $DOCKER_USER/taskboard-backend:v1 app/backend
```

```bash
docker build -t $DOCKER_USER/taskboard-frontend:v1 app/frontend
```

```bash
docker images | grep taskboard
```

**Expected:** two images tagged `v1`. The backend is about 150-200 MB and the frontend about 50-60 MB.

### Step 1.4: Test the whole stack with Docker Compose

Compose starts MySQL, the backend and the frontend on one Docker network, in the right order.

```bash
cd app && docker compose up -d --build && docker compose ps
```

Wait until `db` shows `healthy`, then test the API through the frontend:

```bash
curl -s localhost:8080/api/ready; echo; curl -s localhost:8080/api/info; echo
```

**Expected:**

```text
{"status":"ready"}
{"environment":"docker-compose","namespace":"local","node":"local","pod":"<container-id>","version":"v1"}
```

Open `http://<EC2-PUBLIC-IP>:8080` in the browser. Add 3 tasks with different priorities and move one to **In Progress** with the arrow button. The footer shows which container served you.

**Data survives a restart because of the named volume:**

```bash
docker compose restart backend && sleep 3 && curl -s localhost:8080/api/tasks | head -c 300; echo
```

Stop everything and delete the volume:

```bash
docker compose down -v && cd ..
```

### Step 1.5: Push to Docker Hub

```bash
docker login -u $DOCKER_USER
```

```bash
docker push $DOCKER_USER/taskboard-backend:v1 && docker push $DOCKER_USER/taskboard-frontend:v1
```

Check `https://hub.docker.com/u/<your-username>`: both repositories show the `v1` tag.

**No Docker Hub?** Load the images straight into Kind instead (Kind nodes cannot see your local Docker images otherwise):

```bash
kind load docker-image $DOCKER_USER/taskboard-backend:v1 $DOCKER_USER/taskboard-frontend:v1 --name <your-kind-cluster-name>
```

This works for Kind only. The AWS cluster in Phase 6 needs Docker Hub.

### Phase 1 checkpoint

- [ ] Both images build without errors
- [ ] The app works with Docker Compose in the browser
- [ ] Both `v1` images are on Docker Hub

## 4. Phase 2: Deploy on Kind with plain YAML

You apply one file at a time and check it before moving on. This is how real teams debug: build the stack bottom-up (config, then database, then API, then UI).

### Step 2.1: Put your Docker Hub user into the YAML

```bash
sed -i "s/YOUR_DOCKERHUB_USER/$DOCKER_USER/g" k8s/*.yaml && grep -n 'image:' k8s/*.yaml
```

**Expected:** the backend and frontend images now start with your username.

### Step 2.2: Check the default StorageClass (Module 7)

```bash
kubectl get storageclass
```

**Expected (Kind):** `standard (default)` with provisioner `rancher.io/local-path`. The MySQL PVC will be created from it automatically.

### Step 2.3: Label one worker for the database (Module 10)

MySQL keeps its data on one node's disk. We decide that node in advance, before MySQL starts.

```bash
DB_NODE=$(kubectl get nodes -o name -l '!node-role.kubernetes.io/control-plane' | head -1) && kubectl label $DB_NODE role=db && kubectl get nodes -L role
```

**Expected:** exactly one worker shows `db` in the `ROLE` column.

**Do this before Step 2.6.** If MySQL starts on another node first, its volume stays on that node and the pod can never move to the `role=db` node.

### Step 2.4: Namespace (Module 3)

```bash
kubectl apply -f k8s/00-namespace.yaml && kubectl config set-context --current --namespace=taskboard
```

The second command makes `taskboard` the default namespace, so you can skip `-n taskboard` from now on.

### Step 2.5: ConfigMap and Secret (Module 6)

```bash
kubectl apply -f k8s/01-configmap.yaml -f k8s/02-secret.yaml && kubectl get configmap,secret
```

See that a Secret is only base64-encoded, not encrypted:

```bash
kubectl get secret taskboard-db-secret -o jsonpath='{.data.MYSQL_PASSWORD}' | base64 -d; echo
```

| Key | Where | Read by |
| --- | --- | --- |
| `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `APP_ENV` | ConfigMap | Backend (`envFrom`) |
| `MYSQL_DATABASE`, `MYSQL_USER` | ConfigMap | MySQL image on first start |
| `MYSQL_PASSWORD` | Secret | MySQL and the backend (as `DB_PASSWORD`) |
| `MYSQL_ROOT_PASSWORD` | Secret | MySQL only |

### Step 2.6: MySQL StatefulSet (Modules 4, 7, 8, 10)

```bash
kubectl apply -f k8s/03-mysql.yaml && kubectl get pods -w
```

Wait for `taskboard-mysql-0` to show `1/1 Running` (about 60 seconds), then press `Ctrl+C`.

```bash
kubectl get pvc,pv && kubectl get pod taskboard-mysql-0 -o wide
```

**Check three things:**

1. The pod name ends in `-0`. A StatefulSet gives stable, ordered names.
2. The PVC `mysql-data-taskboard-mysql-0` is `Bound`. It was created from `volumeClaimTemplates`.
3. The `NODE` column is the node you labelled `role=db`.

Log in to MySQL and confirm the database exists:

```bash
kubectl exec -it taskboard-mysql-0 -- sh -c 'mysql -utaskboard -p"$MYSQL_PASSWORD" -e "SHOW DATABASES;"'
```

**Expected:** `taskboard` is in the list.

### Step 2.7: Backend API (Modules 3, 4, 6)

```bash
kubectl apply -f k8s/04-backend.yaml && kubectl rollout status deployment taskboard-backend
```

```bash
kubectl get pods -l app.kubernetes.io/name=backend -o wide && kubectl get endpointslices -l kubernetes.io/service-name=taskboard-backend
```

**Expected:** 2 backend pods `1/1 Running`, ideally on different workers, and 2 IPs in the endpoints.

Call the API from inside the cluster through the Service name:

```bash
kubectl run curl --image=busybox:1.37 --rm -it --restart=Never -- wget -qO- http://taskboard-backend:5000/api/info
```

**Expected:** JSON with `pod`, `node` and `"version":"v1"`. Run it 3 times: the pod name changes, because the Service load-balances.

### Step 2.8: Frontend and NodePort (Modules 3, 4)

```bash
kubectl apply -f k8s/05-frontend.yaml && kubectl rollout status deployment taskboard-frontend && kubectl get svc
```

**Expected:** `taskboard-frontend` is `NodePort` with `80:30080/TCP`.

Kind nodes are Docker containers, so the NodePort is not reachable from the internet. Use port-forward on the Kind machine:

```bash
kubectl port-forward svc/taskboard-frontend 8080:80 --address=0.0.0.0
```

Open `http://<EC2-PUBLIC-IP>:8080`. Add a few tasks. Click **Who served me?** several times: the backend pod name in the footer changes.

Keep the port-forward running in this terminal and open a second terminal for the next steps.

### Step 2.9: Prove the data is persistent (Module 7)

```bash
kubectl delete pod taskboard-mysql-0 && kubectl get pods -w
```

Wait for `taskboard-mysql-0` to be `Running` again, then refresh the browser. **All tasks are still there.** The new pod got the same name and re-attached the same PVC.

### Step 2.10: Ingress (Module 5, optional on Kind)

Skip this step if you do not have Traefik. To install it:

```bash
helm repo add traefik https://traefik.github.io/charts && helm repo update && helm install traefik traefik/traefik -n traefik --create-namespace
```

```bash
kubectl apply -f k8s/06-ingress.yaml && kubectl get ingress
```

```bash
kubectl port-forward -n traefik svc/traefik 8081:80 --address=0.0.0.0
```

Open `http://<EC2-PUBLIC-IP>:8081` (allow TCP 8081 in the Security Group). Traefik sends `/api` straight to the backend Service and everything else to the frontend.

### Phase 2 checkpoint

```bash
kubectl get all,pvc
```

- [ ] 1 MySQL pod, 2 backend pods and 2 frontend pods are `Running`
- [ ] The UI works and the footer shows different backend pods
- [ ] Tasks survive a MySQL pod delete

## 5. Phase 3: Production hardening

The app works. Now make it behave like a production system: it must survive failures, use resources fairly, spread across nodes and scale on its own.

### Step 3.1: Readiness vs liveness, live (Module 3)

The backend already has both probes. Stop the database and watch what happens:

```bash
kubectl scale statefulset taskboard-mysql --replicas=0 && sleep 25 && kubectl get pods -l app.kubernetes.io/name=backend
```

**Expected:** backend pods show `0/1 Running` with **0 restarts**.

| Probe | Path | Result while MySQL is down |
| --- | --- | --- |
| Readiness | `/api/ready` | Fails: the pod is removed from the Service endpoints, so it gets no traffic |
| Liveness | `/api/health` | Passes: Kubernetes does not restart the pod |

Bring MySQL back. The backend pods return to `1/1` without a restart:

```bash
kubectl scale statefulset taskboard-mysql --replicas=1 && kubectl get pods -w
```

### Step 3.2: See the scheduling rules (Module 10)

```bash
kubectl get pods -o wide && kubectl get nodes -L role
```

| Rule | Where | Effect |
| --- | --- | --- |
| Node affinity `role In [db]` (required) | MySQL | MySQL only runs on the labelled node. If that node is gone, MySQL waits as `Pending` instead of starting empty somewhere else |
| Pod anti-affinity on `kubernetes.io/hostname` (preferred) | Backend | The 2 replicas go to different nodes when possible, so one node failure does not take down the API |
| Control-plane taint `NoSchedule` | Master node | No app pods run on the master |

```bash
kubectl describe node $(kubectl get nodes -o name -l node-role.kubernetes.io/control-plane | head -1 | cut -d/ -f2) | grep Taints
```

### Step 3.3: Log agent DaemonSet (Modules 9, 10)

```bash
kubectl apply -f k8s/10-log-agent.yaml && kubectl get daemonset taskboard-log-agent && kubectl get pods -l app.kubernetes.io/name=log-agent -o wide
```

**Expected:** `DESIRED 3`, `READY 3`, one pod on **every** node, including the control plane. The toleration in the DaemonSet allows it onto the master.

```bash
kubectl logs -l app.kubernetes.io/name=log-agent --prefix --tail=2
```

**Expected:** each node reports how many TaskBoard log lines it holds. Only nodes running TaskBoard pods have lines above 0.

### Step 3.4: Metrics Server (Module 11)

Skip if `kubectl top nodes` already works.

```bash
kubectl apply -f https://github.com/kubernetes-sigs/metrics-server/releases/latest/download/components.yaml
```

```bash
kubectl patch deployment metrics-server -n kube-system --type=json -p '[{"op":"add","path":"/spec/template/spec/containers/0/args/-","value":"--kubelet-insecure-tls"}]'
```

```bash
kubectl rollout status deployment metrics-server -n kube-system && sleep 30 && kubectl top pods
```

### Step 3.5: Horizontal Pod Autoscaler (Module 11)

```bash
kubectl apply -f k8s/07-hpa.yaml && kubectl get hpa
```

**Expected:** `TARGETS cpu: 1%/50%` (low), `MINPODS 2`, `MAXPODS 6`. The percentage is measured against the CPU **request** (100m), not the limit.

Start a load generator that calls `/api/stress` 4 times in parallel, forever:

```bash
kubectl run load --image=busybox:1.37 --restart=Never -- sh -c 'while true; do for i in 1 2 3 4; do wget -qO- "http://taskboard-backend:5000/api/stress?seconds=10" >/dev/null & done; wait; done'
```

Watch the HPA (in a second terminal):

```bash
kubectl get hpa taskboard-backend -w
```

**Expected within 1-2 minutes:** CPU goes well above 50% and `REPLICAS` grows from 2 towards 6. New backend pods appear with `kubectl get pods`.

Stop the load and watch the scale down (it waits 60 seconds of low CPU first, as set in `behavior.scaleDown`):

```bash
kubectl delete pod load && kubectl get hpa taskboard-backend -w
```

**Note:** from now on the HPA owns the replica count. If you re-apply `04-backend.yaml`, its `replicas: 2` resets the count once, and the HPA corrects it again.

### Step 3.6: PodDisruptionBudget and node drain

```bash
kubectl apply -f k8s/08-pdb.yaml && kubectl get pdb
```

Drain the worker that is **not** the database node:

```bash
APP_NODE=$(kubectl get nodes -o name -l '!node-role.kubernetes.io/control-plane,!role' | head -1 | cut -d/ -f2) && kubectl drain $APP_NODE --ignore-daemonsets --delete-emptydir-data
```

**Expected:** the drain evicts pods one by one and respects the PDB (at least 1 backend pod always running). The UI keeps working in the browser the whole time.

```bash
kubectl uncordon $APP_NODE && kubectl get nodes
```

**Why not drain the database node?** MySQL is pinned there by required node affinity. Draining it would leave MySQL `Pending` until you uncordon. That is the trade-off of local storage.

### Step 3.7: VPA recommendations for MySQL (Module 11, optional)

Needs the VPA components installed as in Module 11.

```bash
kubectl apply -f k8s/09-vpa.yaml && sleep 120 && kubectl describe vpa taskboard-mysql | sed -n '/Recommendation/,$p'
```

In `Off` mode VPA only shows `Lower Bound`, `Target` and `Upper Bound`. It changes nothing. Compare the `Target` with the request in `03-mysql.yaml` (250m CPU, 512Mi memory).

### Phase 3 checkpoint

- [ ] Backend pods go unready (not restarted) when MySQL is down
- [ ] One log-agent pod runs on every node
- [ ] The HPA scaled the backend up under load and back down after
- [ ] The app stayed up during a node drain

## 6. Phase 4: Rolling update to v2 and rollback

You ship a new backend version with zero downtime, then roll it back. The backend Deployment uses `maxSurge: 1` and `maxUnavailable: 0`: a new pod must be ready before an old one is removed.

### Step 4.1: Build and push v2

No code change is needed. The version is a build argument:

```bash
docker build --build-arg APP_VERSION=v2 -t $DOCKER_USER/taskboard-backend:v2 app/backend && docker push $DOCKER_USER/taskboard-backend:v2
```

On Kind without Docker Hub, load it instead:

```bash
kind load docker-image $DOCKER_USER/taskboard-backend:v2 --name <your-kind-cluster-name>
```

### Step 4.2: Watch the version during the update

In a second terminal, ask the API for its version every second:

```bash
kubectl run watcher --image=busybox:1.37 --rm -it --restart=Never -- sh -c 'while true; do wget -qO- http://taskboard-backend:5000/api/info | grep -o "version\":\"v[0-9]"; sleep 1; done'
```

### Step 4.3: Roll out v2

```bash
kubectl set image deployment/taskboard-backend backend=$DOCKER_USER/taskboard-backend:v2 && kubectl annotate deployment/taskboard-backend kubernetes.io/change-cause="backend v2" --overwrite
```

```bash
kubectl rollout status deployment taskboard-backend
```

**Watch the second terminal:** answers move from `v1` to a mix of `v1` and `v2`, then only `v2`. There is no error line in between: that is the zero-downtime rolling update. Refresh the browser: the footer shows `version v2`.

### Step 4.4: History and rollback

```bash
kubectl rollout history deployment taskboard-backend
```

```bash
kubectl rollout undo deployment taskboard-backend && kubectl rollout status deployment taskboard-backend
```

**Expected:** the watcher goes back to `v1`. Rollback is just another rolling update to the previous ReplicaSet.

```bash
kubectl get replicasets -l app.kubernetes.io/name=backend
```

You see one ReplicaSet per version. The old one is kept at 0 pods (`revisionHistoryLimit: 5`), which is what makes a fast rollback possible.

Stop the watcher with `Ctrl+C`.

### Step 4.5: What a bad release looks like

Deploy a tag that does not exist:

```bash
kubectl set image deployment/taskboard-backend backend=$DOCKER_USER/taskboard-backend:v9 && kubectl get pods -l app.kubernetes.io/name=backend
```

**Expected:** one new pod in `ErrImagePull` / `ImagePullBackOff`, and **all old pods still Running**. Because `maxUnavailable: 0`, the broken release never takes the app down. Fix it:

```bash
kubectl rollout undo deployment taskboard-backend
```

### Phase 4 checkpoint

- [ ] The footer showed `v2` after the rollout
- [ ] No failed requests during the update
- [ ] Rollback returned to `v1`
- [ ] A bad image tag did not break the running app

## 7. Phase 5: Package everything as a Helm chart

Eleven YAML files are hard to ship to many environments. The chart in `helm/taskboard` creates the same objects from one command, with different values for dev and prod.

### Step 5.1: Tour the chart

```bash
tree helm/taskboard && cat helm/taskboard/values.yaml
```

| File | Role |
| --- | --- |
| `Chart.yaml` | Chart name `taskboard`, chart version `1.0.0`, app version `v1` |
| `values.yaml` | Defaults for every setting (images, replicas, resources, features on/off) |
| `values-dev.yaml` | Kind: 1 replica each, no HPA, no PDB |
| `values-prod.yaml` | AWS: 2 replicas, HPA on, PDB on, 5Gi database |
| `templates/_helpers.tpl` | Shared names, labels and the image name helper |
| `templates/*.yaml` | One template per component. Ingress, HPA, PDB, VPA and the log agent have an `enabled` switch |
| `templates/NOTES.txt` | The message printed after install |
| `templates/tests/test-connection.yaml` | `helm test` pod that checks the frontend and the backend |

Two production practices in the templates:

- **`required`** in `secret.yaml`: the install fails with a clear message if you forget a password.
- **`checksum/config` and `checksum/secret` annotations** on the backend pods: when a ConfigMap or Secret value changes, the hash changes and Helm restarts the pods so they read the new values.

### Step 5.2: Keep passwords out of Git

Create a local values file that only holds secrets. Never commit it.

```bash
printf 'secrets:\n  mysqlRootPassword: Root-Pass-2026\n  mysqlPassword: TaskBoard-Pass-2026\n' > my-secrets.yaml && echo 'my-secrets.yaml' >> .gitignore && cat my-secrets.yaml
```

### Step 5.3: Lint and preview

```bash
helm lint helm/taskboard -f helm/taskboard/values-dev.yaml -f my-secrets.yaml --set imageUser=$DOCKER_USER
```

```bash
helm template taskboard helm/taskboard -n taskboard -f helm/taskboard/values-dev.yaml -f my-secrets.yaml --set imageUser=$DOCKER_USER | grep -E '^kind:|image:'
```

**Expected:** `1 chart(s) linted, 0 chart(s) failed`, and a list of kinds with your image names. Try the safety net once without the secrets file:

```bash
helm template taskboard helm/taskboard --set imageUser=$DOCKER_USER
```

**Expected:** `Error: ... Set secrets.mysqlRootPassword ...`

### Step 5.4: Remove the plain-YAML version

Helm cannot adopt objects created by `kubectl apply`, so delete them first. The namespace delete also removes the PVC, so the old tasks are gone.

```bash
kubectl delete namespace taskboard && kubectl config set-context --current --namespace=default
```

The `role=db` node label stays. The chart uses it too.

### Step 5.5: Install the dev release

```bash
helm install taskboard helm/taskboard -n taskboard --create-namespace -f helm/taskboard/values-dev.yaml -f my-secrets.yaml --set imageUser=$DOCKER_USER
```

| Part | Meaning |
| --- | --- |
| `taskboard` | Release name. Every object is named `taskboard-...` |
| `helm/taskboard` | Chart from the local folder |
| `-n taskboard --create-namespace` | Install into this namespace, create it if missing |
| `-f values-dev.yaml -f my-secrets.yaml` | Value files, later files win |
| `--set imageUser=...` | Highest priority, wins over every file |

```bash
kubectl get pods -n taskboard -w
```

When everything is `Running`, run the chart's own test and open the app:

```bash
helm test taskboard -n taskboard
```

```bash
kubectl port-forward svc/taskboard-frontend -n taskboard 8080:80 --address=0.0.0.0
```

**Expected:** `Phase: Succeeded` for the test, and the UI at `http://<EC2-PUBLIC-IP>:8080` shows `env kind-dev` in the footer.

### Step 5.6: Upgrade, history, rollback

Ship v2 through Helm:

```bash
helm upgrade taskboard helm/taskboard -n taskboard -f helm/taskboard/values-dev.yaml -f my-secrets.yaml --set imageUser=$DOCKER_USER --set backend.image.tag=v2
```

```bash
helm history taskboard -n taskboard
```

```bash
helm rollback taskboard 1 -n taskboard && helm history taskboard -n taskboard
```

**Expected:** revisions 1 (install), 2 (upgrade to v2), 3 (rollback to 1). The footer goes v1, then v2, then v1.

**Remember:** `helm upgrade` does not keep the `--set` flags from the last run. Always pass all your value files and flags again.

### Phase 5 checkpoint

- [ ] `helm lint` passes and a missing password fails early
- [ ] `helm test` succeeds
- [ ] Upgrade to v2 and rollback both worked

## 8. Phase 6: Production deploy on the kubeadm AWS cluster

The same chart now goes to the real 3-node cluster from Module 13 (`master`, `worker-1`, `worker-2`). Only the values file changes. Run every command on the **master** unless a step says otherwise.

### Step 6.1: Check the cluster

```bash
kubectl get nodes -o wide
```

**Expected:** 3 nodes `Ready`. If not, rebuild the cluster with `k8s-setup.sh` (Module 13).

**Security Group check (from Module 13):** "All traffic, source = the same SG" rule, and TCP `30000-32767` from your IP.

### Step 6.2: Install Helm and copy the project

```bash
curl -fsSL https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-4 | bash && helm version --short
```

From your laptop:

```bash
scp -i <key.pem> taskboard.zip ubuntu@<MASTER-PUBLIC-IP>:~
```

On the master:

```bash
sudo apt-get install -y unzip && unzip -o taskboard.zip && cd taskboard && export DOCKER_USER=<your-dockerhub-username>
```

### Step 6.3: Add a StorageClass (Module 7)

Kind ships a default StorageClass. A kubeadm cluster has none, so the MySQL PVC would stay `Pending`. Install the Local Path Provisioner and make it the default:

```bash
kubectl apply -f https://raw.githubusercontent.com/rancher/local-path-provisioner/v0.0.36/deploy/local-path-storage.yaml
```

```bash
kubectl patch storageclass local-path -p '{"metadata":{"annotations":{"storageclass.kubernetes.io/is-default-class":"true"}}}' && kubectl get storageclass
```

**Expected:** `local-path (default)`.

### Step 6.4: Label the database node and add Metrics Server

```bash
kubectl label node worker-1 role=db && kubectl get nodes -L role
```

```bash
kubectl apply -f https://github.com/kubernetes-sigs/metrics-server/releases/latest/download/components.yaml && kubectl patch deployment metrics-server -n kube-system --type=json -p '[{"op":"add","path":"/spec/template/spec/containers/0/args/-","value":"--kubelet-insecure-tls"}]'
```

### Step 6.5: Install the prod release

```bash
printf 'secrets:\n  mysqlRootPassword: Prod-Root-2026\n  mysqlPassword: Prod-App-2026\n' > my-secrets.yaml
```

```bash
helm install taskboard helm/taskboard -n taskboard --create-namespace -f helm/taskboard/values-prod.yaml -f my-secrets.yaml --set imageUser=$DOCKER_USER
```

```bash
kubectl get pods -n taskboard -o wide -w
```

**Expected after about 90 seconds:**

| Pod | Count | Node |
| --- | --- | --- |
| `taskboard-mysql-0` | 1 | worker-1 (the `role=db` node) |
| `taskboard-backend-...` | 2 | one on worker-1, one on worker-2 (anti-affinity) |
| `taskboard-frontend-...` | 2 | spread over the workers |
| `taskboard-log-agent-...` | 3 | master, worker-1, worker-2 |

```bash
kubectl get pvc,hpa,pdb -n taskboard && helm test taskboard -n taskboard
```

### Step 6.6: Open the app on every node

Open all three in the browser:

```text
http://<MASTER-PUBLIC-IP>:30080
http://<WORKER1-PUBLIC-IP>:30080
http://<WORKER2-PUBLIC-IP>:30080
```

**Expected:** the same board on all three, with `env aws-prod` in the footer. A NodePort is open on every node, and kube-proxy forwards to a pod on any node. If only one IP works, the Security Group "All traffic, same SG" rule is missing.

### Step 6.7: Production drills

Run these one by one and keep the browser open.

**Drill A: autoscaling under load**

```bash
kubectl run load -n taskboard --image=busybox:1.37 --restart=Never -- sh -c 'while true; do for i in 1 2 3 4; do wget -qO- "http://taskboard-backend:5000/api/stress?seconds=10" >/dev/null & done; wait; done'
```

```bash
kubectl get hpa -n taskboard -w
```

When replicas reach 4-6, stop the load: `kubectl delete pod load -n taskboard`.

**Drill B: lose a worker for maintenance**

```bash
kubectl drain worker-2 --ignore-daemonsets --delete-emptydir-data && kubectl get pods -n taskboard -o wide
```

The app stays up, and the worker-2 IP still serves the page. Bring it back with `kubectl uncordon worker-2`.

**Drill C: database pod crash**

```bash
kubectl delete pod taskboard-mysql-0 -n taskboard && kubectl get pods -n taskboard -w
```

The tasks are still there after the pod restarts.

**Drill D: release v2 to production**

```bash
helm upgrade taskboard helm/taskboard -n taskboard -f helm/taskboard/values-prod.yaml -f my-secrets.yaml --set imageUser=$DOCKER_USER --set backend.image.tag=v2 && kubectl rollout status deployment taskboard-backend -n taskboard
```

The footer shows `v2`. Roll back with `helm rollback taskboard -n taskboard`.

### Phase 6 checkpoint

- [ ] All pods `Running` and on the expected nodes
- [ ] The app opens on all three public IPs
- [ ] HPA, drain, database crash and v2 release drills all passed

## 9. Troubleshooting, cleanup and assignments

### 9.1 Troubleshooting

Start every investigation with these three commands:

```bash
kubectl get pods -n taskboard -o wide && kubectl get events -n taskboard --sort-by=.lastTimestamp | tail -15
```

```bash
kubectl describe pod <pod-name> -n taskboard | tail -25
```

```bash
kubectl logs <pod-name> -n taskboard --previous
```

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| `ErrImagePull` / `ImagePullBackOff` | Wrong Docker Hub user or tag, or the image was never pushed | `kubectl describe pod` shows the image name. Check `grep image: k8s/*.yaml` or `--set imageUser` |
| MySQL pod `Pending`, event `didn't match Pod's node affinity` | No node has the `role=db` label | `kubectl label node <worker> role=db` |
| MySQL pod `Pending`, PVC `Pending` (kubeadm) | No default StorageClass | Step 6.3 (Local Path Provisioner) |
| MySQL pod `Pending`, event `volume node affinity conflict` | The PVC was created on another node before you added the label | Delete the release or namespace (and the PVC), label the node, deploy again |
| Backend `0/1 Running`, never ready | Backend cannot reach MySQL: wrong password, MySQL still starting, or wrong `DB_HOST` | `kubectl logs deploy/taskboard-backend`, then `kubectl exec` into MySQL and log in with the same password |
| Backend `CrashLoopBackOff` | The app crashed at start | `kubectl logs <pod> --previous` |
| UI loads, but shows `Could not load tasks` | Frontend cannot reach the backend Service | Check `BACKEND_URL` in the frontend Deployment and `kubectl get endpointslices -n taskboard` |
| Browser cannot open the NodePort (kubeadm) | Security Group | TCP 30000-32767 from your IP; "All traffic, same SG" for cross-node traffic |
| Browser cannot open the NodePort (Kind) | Kind nodes are containers | Use `kubectl port-forward ... --address=0.0.0.0` |
| HPA `TARGETS <unknown>/50%` | Metrics Server missing or not ready | Step 3.4. Wait 1 minute after install |
| `helm install` fails: `cannot re-use a name` | The release exists | `helm upgrade` instead, or `helm uninstall taskboard -n taskboard` |
| `helm install` fails: `exists and cannot be imported` | Objects from plain YAML are still there | Step 5.4 |
| `Error: ... Set secrets.mysqlRootPassword` | Secrets file not passed | Add `-f my-secrets.yaml` |
| Changed password, MySQL still uses the old one | MySQL reads the password only on the first start, when the volume is empty | Change it inside MySQL, or delete the PVC to start fresh (data is lost) |

### 9.2 Cleanup

**Helm release (Kind or AWS):**

```bash
helm uninstall taskboard -n taskboard && kubectl delete pvc --all -n taskboard && kubectl delete namespace taskboard
```

`helm uninstall` does not delete the StatefulSet's PVC on purpose, so database data is never lost by accident. Delete it yourself when you are sure.

**Labels and local images:**

```bash
kubectl label nodes --all role- && docker image prune -f
```

**Docker Compose leftovers:**

```bash
cd app && docker compose down -v && cd ..
```

### 9.3 Student assignments

1. **Edit a task title.** The API already supports `PUT /api/tasks/<id>` with `title`. Add an edit button in `app.js`, build the frontend as `v2`, and roll it out with Helm.
2. **Ingress on AWS.** Install Traefik on the kubeadm cluster with a NodePort service, set `ingress.enabled=true` in `values-prod.yaml`, and reach the app through Traefik's NodePort.
3. **Resource tuning.** After the HPA drill, read the VPA recommendation for MySQL (Step 3.7) and update `mysql.resources` in `values-prod.yaml`. Explain your numbers.
4. **Backup job.** Write a Kubernetes CronJob that runs `mysqldump` every hour into a second PVC. Prove a restore works.
5. **Break and fix.** Your partner changes one thing (a label, a port, a password, a probe path). Find and fix it in under 10 minutes using only `kubectl get`, `describe`, `logs` and `events`.
6. **Write-up.** Draw the architecture of your deployment and explain, in one paragraph each, why MySQL is a StatefulSet, why the backend has two probes, and why the app survived the node drain.

### 9.4 Next modules

- **Module 14, Cert-Manager:** HTTPS for the TaskBoard Ingress with a real certificate.
- **Module 15, Argo CD:** push the Helm chart to Git and let Argo CD deploy every change.
- **Module 16, Capstone:** split the backend into microservices and add monitoring.
