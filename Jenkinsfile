
// =============================================================================
// AegisPilot / Aegisops — Production Declarative Jenkinsfile
//
// CI/CD lifecycle:
// source checkout -> dependencies -> tests -> coverage -> static analysis ->
// optional SonarQube quality gate -> immutable image -> ECR -> blue/green
// Kubernetes deployment -> candidate smoke test -> manual promotion ->
// active-service smoke test -> deployment metadata.
// =============================================================================

pipeline {
    agent any

    parameters {
        booleanParam(
            name: 'DEPLOY_ENABLED',
            defaultValue: false,
            description: 'Build and test only by default. Enable only for an approved K3s deployment.'
        )

        booleanParam(
            name: 'RUN_SONAR',
            defaultValue: false,
            description: 'Run SonarQube analysis and enforce its configured quality gate.'
        )

        string(
            name: 'K8S_COMMIT',
            defaultValue: 'a4e819db6b9eecb58fab7e10dbb96daa993ae520',
            trim: true,
            description: 'Reviewed feature/k8s commit used as read-only deployment assets (no live-snapshot overlay).'
        )
    }

    environment {
        APP_NAME = 'aegis-warroom'
        NAMESPACE = 'aegispilot'

        ECR_REGION = 'ap-south-1'
        ECR_REGISTRY = '850887971586.dkr.ecr.ap-south-1.amazonaws.com'
        ECR_REPOSITORY = 'aegispilot/warroom'

        K8S_MANIFEST_DIR = 'k8s/warroom'
        K8S_ASSETS_DIR = '.k8s-assets'

        ACTIVE_SERVICE = 'aegis-warroom'
        BLUE_DEPLOYMENT = 'aegis-warroom-blue'
        GREEN_DEPLOYMENT = 'aegis-warroom-green'

        KUBECONFIG_CREDENTIALS_ID = 'k3s-kubeconfig'

        SONAR_SERVER = 'team3-sonar'
        REPORTS_DIR = 'reports'

        // Configure this in Jenkins for live deployment metadata publication.
        AEGIS_API_URL = "${env.AEGIS_API_URL ?: ''}"
    }

    options {
        timestamps()
        timeout(time: 30, unit: 'MINUTES')
        disableConcurrentBuilds()
    }

    stages {

        stage('Checkout') {
            steps {
                echo "▶ Checking out application source..."
                checkout scm
            }
        }

        stage('Environment / Version') {
            steps {
                script {
                    def commit = sh(
                        returnStdout: true,
                        script: 'git rev-parse HEAD'
                    ).trim()

                    env.APP_COMMIT = commit
                    env.IMAGE_TAG = "${commit.take(7)}-${env.BUILD_NUMBER}"
                    env.IMAGE = "${env.ECR_REGISTRY}/${env.ECR_REPOSITORY}:${env.IMAGE_TAG}"
                    env.TRAFFIC_PROMOTED = 'false'
                }

                echo "============================================================"
                echo "▶ Application Commit : ${env.APP_COMMIT}"
                echo "▶ Build Version      : ${env.IMAGE_TAG}"
                echo "▶ Target Service     : ${env.APP_NAME}"
                echo "▶ Namespace          : ${env.NAMESPACE}"
                echo "▶ Target Image       : ${env.IMAGE}"
                echo "============================================================"

                sh 'mkdir -p reports'
            }
        }

        stage('Install Dependencies') {
            steps {
                echo "▶ Preparing isolated Python build environment..."

                sh '''
                    set -eu

                    python3 -m venv .venv || python -m venv .venv

                    . .venv/bin/activate || . .venv/Scripts/activate

                    export PIP_DEFAULT_TIMEOUT=120
                    export PIP_RETRIES=5
                    export PYTHONUNBUFFERED=1

                    echo "▶ Upgrading pip..."
                    python -m pip install --upgrade pip

                    # Retry pip on transient PyPI / network timeouts (seen on Jenkins agents).
                    attempt=1
                    max_attempts=3
                    until python -m pip install -r backend/requirements.txt; do
                      if [ "$attempt" -ge "$max_attempts" ]; then
                        echo "pip install failed after ${max_attempts} attempts"
                        exit 1
                      fi
                      echo "pip install attempt ${attempt} failed; retrying in 15s..."
                      attempt=$((attempt + 1))
                      sleep 15
                    done

                    echo "▶ Installing test tools..."
                    python -m pip install pytest pytest-cov pytest-asyncio flake8 httpx ruff
                    echo "▶ Dependencies ready."
                '''
            }
        }

        stage('Unit Tests') {
            steps {
                echo "▶ Running unit tests with JUnit XML reporting..."

                sh '''
                    set -eu

                    . .venv/bin/activate || . .venv/Scripts/activate

                    python -m pytest tests/ \
                        -q \
                        --junitxml=reports/junit.xml
                '''
            }
        }

        stage('Coverage Gate') {
            steps {
                echo "▶ Enforcing minimum test coverage threshold (>= 85%)..."

                sh '''
                    set -eu

                    . .venv/bin/activate || . .venv/Scripts/activate

                    python -m pytest tests/ \
                        --cov=backend \
                        --cov-report=xml:reports/coverage.xml \
                        --cov-report=term \
                        --cov-fail-under=85 \
                        --cov-config=.coveragerc
                '''
            }
        }

        stage('Static Analysis (Ruff)') {
            steps {
                echo "▶ Running Python static code analysis with Ruff..."

                sh '''
                    set -eu

                    . .venv/bin/activate || . .venv/Scripts/activate

                    ruff check backend --select F
                '''
            }
        }

        stage('SonarQube Quality Gate') {
            when {
                expression {
                    return params.RUN_SONAR
                }
            }

            steps {
                echo "▶ Running SonarQube Scanner analysis..."

                script {
                    def scannerHome = tool 'SonarScanner'

                    withSonarQubeEnv(env.SONAR_SERVER) {
                        withEnv([
                            "SONAR_SCANNER_HOME=${scannerHome}",
                            "PATH+SONAR=${scannerHome}/bin"
                        ]) {
                            sh './scripts/run_sonar.sh'
                        }
                    }
                }

                timeout(time: 10, unit: 'MINUTES') {
                    waitForQualityGate abortPipeline: true
                }
            }
        }

        stage('Checkout Pinned Kubernetes Assets') {
            when {
                expression {
                    return params.DEPLOY_ENABLED
                }
            }

            steps {
                dir(env.K8S_ASSETS_DIR) {
                    checkout([
                        $class: 'GitSCM',
                        branches: [[name: params.K8S_COMMIT]],
                        doGenerateSubmoduleConfigurations: false,
                        extensions: [
                            [$class: 'CleanBeforeCheckout']
                        ],
                        userRemoteConfigs: [[
                            url: 'https://github.com/HariniKartheeswaran/AegisPilot--Predictive-Agentic-Self-Healing-SRE-Platform.git'
                        ]]
                    ])

                    script {
                        def resolved = sh(
                            returnStdout: true,
                            script: 'git rev-parse HEAD'
                        ).trim()

                        if (resolved != params.K8S_COMMIT) {
                            error(
                                "Kubernetes checkout resolved ${resolved}, expected ${params.K8S_COMMIT}"
                            )
                        }
                    }
                }

                echo "▶ Kubernetes assets pinned to ${params.K8S_COMMIT}"
            }
        }

        stage('Build and Push Image to ECR') {
            when {
                expression {
                    return params.DEPLOY_ENABLED
                }
            }

            steps {
                echo "▶ Building immutable image ${env.IMAGE}..."

                sh '''
                    set -eu

                    command -v aws >/dev/null
                    command -v docker >/dev/null

                    echo "▶ Checking AWS identity..."
                    aws sts get-caller-identity

                    echo "▶ Checking ECR access..."
                    aws ecr get-login-password --region "$ECR_REGION" \
                      | docker login \
                          --username AWS \
                          --password-stdin "$ECR_REGISTRY"

                    docker build \
                      -f docker/Dockerfile \
                      -t "$IMAGE" \
                      .

                    docker push "$IMAGE"
                '''
            }
        }

        stage('Prepare Kubernetes and Select Candidate') {
            when {
                expression {
                    return params.DEPLOY_ENABLED
                }
            }

            steps {
                withCredentials([
                    file(
                        credentialsId: env.KUBECONFIG_CREDENTIALS_ID,
                        variable: 'KUBECONFIG'
                    )
                ]) {
                    script {
                        sh '''
                            set -eu

                            command -v kubectl >/dev/null

                            test -f "$K8S_ASSETS_DIR/k8s/namespace.yaml"
                            test -f "$K8S_ASSETS_DIR/$K8S_MANIFEST_DIR/configmap.yaml"
                            test -f "$K8S_ASSETS_DIR/$K8S_MANIFEST_DIR/service.yaml"

                            kubectl apply \
                              -f "$K8S_ASSETS_DIR/k8s/namespace.yaml"

                            kubectl apply \
                              -f "$K8S_ASSETS_DIR/$K8S_MANIFEST_DIR/configmap.yaml"

                            kubectl apply \
                              -f "$K8S_ASSETS_DIR/$K8S_MANIFEST_DIR/serviceaccount.yaml"

                            kubectl apply \
                              -f "$K8S_ASSETS_DIR/$K8S_MANIFEST_DIR/rbac.yaml"

                            if kubectl -n "$NAMESPACE" get service "$ACTIVE_SERVICE" >/dev/null 2>&1; then
                                echo "▶ Existing service $ACTIVE_SERVICE found; preserving its current selector."
                            else
                                echo "▶ Service $ACTIVE_SERVICE does not exist; creating it from the reviewed manifest."
                                kubectl apply \
                                  -f "$K8S_ASSETS_DIR/$K8S_MANIFEST_DIR/service.yaml"
                            fi

                            kubectl apply \
                              -f "$K8S_ASSETS_DIR/$K8S_MANIFEST_DIR/ingress.yaml"

                            kubectl -n "$NAMESPACE" get secret ecr-pull >/dev/null
                            kubectl -n "$NAMESPACE" get secret aegis-warroom-secrets >/dev/null
                        '''

                        def active = sh(
                            returnStdout: true,
                            script: '''
                                kubectl -n "$NAMESPACE" \
                                  get service "$ACTIVE_SERVICE" \
                                  -o jsonpath="{.spec.selector.slot}"
                            '''
                        ).trim()

                        if (active != 'blue' && active != 'green') {
                            error(
                                "${env.ACTIVE_SERVICE} has no valid blue/green selector " +
                                "(found: '${active}')"
                            )
                        }

                        env.ACTIVE_COLOR = active
                        env.DEPLOY_COLOR = active == 'blue' ? 'green' : 'blue'
                        env.TARGET_DEPLOYMENT =
                            env.DEPLOY_COLOR == 'blue'
                                ? env.BLUE_DEPLOYMENT
                                : env.GREEN_DEPLOYMENT
                    }
                }

                echo "▶ Active slot: ${env.ACTIVE_COLOR}"
                echo "▶ Candidate slot: ${env.DEPLOY_COLOR}"
                echo "▶ Candidate deployment: ${env.TARGET_DEPLOYMENT}"
            }
        }

        stage('Deploy and Verify Candidate') {
            when {
                expression {
                    return params.DEPLOY_ENABLED
                }
            }

            steps {
                withCredentials([
                    file(
                        credentialsId: env.KUBECONFIG_CREDENTIALS_ID,
                        variable: 'KUBECONFIG'
                    )
                ]) {
                    sh '''
                        set -eu

                        kubectl -n "$NAMESPACE" apply \
                          -f "$K8S_ASSETS_DIR/$K8S_MANIFEST_DIR/deployment-${DEPLOY_COLOR}.yaml"

                        kubectl -n "$NAMESPACE" set image \
                          "deployment/${TARGET_DEPLOYMENT}" \
                          warroom="$IMAGE"

                        kubectl -n "$NAMESPACE" annotate \
                          "deployment/${TARGET_DEPLOYMENT}" \
                          image.tag="$IMAGE_TAG" \
                          --overwrite

                        kubectl -n "$NAMESPACE" rollout status \
                          "deployment/${TARGET_DEPLOYMENT}" \
                          --timeout=180s
                    '''
                }
            }
        }

        stage('Smoke Test Candidate') {
            when {
                expression {
                    return params.DEPLOY_ENABLED
                }
            }

            steps {
                withCredentials([
                    file(
                        credentialsId: env.KUBECONFIG_CREDENTIALS_ID,
                        variable: 'KUBECONFIG'
                    )
                ]) {
                    sh 'bash scripts/k8s_candidate_smoke.sh'
                }
            }
        }

        stage('Approve Traffic Promotion') {
            when {
                expression {
                    return params.DEPLOY_ENABLED
                }
            }

            steps {
                timeout(time: 30, unit: 'MINUTES') {
                    input(
                        message: "Candidate ${env.DEPLOY_COLOR} passed smoke checks. Promote ${env.ACTIVE_SERVICE}?",
                        ok: 'Promote traffic'
                    )
                }
            }
        }

        stage('Promote Candidate') {
            when {
                expression {
                    return params.DEPLOY_ENABLED
                }
            }

            steps {
                withCredentials([
                    file(
                        credentialsId: env.KUBECONFIG_CREDENTIALS_ID,
                        variable: 'KUBECONFIG'
                    )
                ]) {
                    sh 'bash "$K8S_ASSETS_DIR/scripts/k8s_promote.sh"'

                    // Guarantee traffic is on the candidate before judges open the UI.
                    sh '''
                        set -eu
                        ns="${K8S_NAMESPACE:-aegispilot}"
                        svc="${ACTIVE_SERVICE:-aegis-warroom}"
                        slot="${DEPLOY_COLOR}"
                        echo "▶ Confirming Service endpoints for slot=${slot}..."
                        ready=0
                        for i in $(seq 1 30); do
                          ep=$(kubectl -n "$ns" get endpoints "$svc" -o jsonpath='{.subsets[*].addresses[*].ip}' 2>/dev/null || true)
                          pod_ip=$(kubectl -n "$ns" get pods -l "app=aegis-warroom,slot=${slot}" \
                            -o jsonpath='{.items[0].status.podIP}' 2>/dev/null || true)
                          if [ -n "$ep" ] && [ -n "$pod_ip" ] && echo "$ep" | grep -q "$pod_ip"; then
                            echo "▶ Endpoints ready: $ep (pod $pod_ip)"
                            ready=1
                            break
                          fi
                          echo "  attempt $i/30 — endpoints='$ep' pod_ip='$pod_ip'"
                          sleep 2
                        done
                        if [ "$ready" -ne 1 ]; then
                          echo "ERROR: Service $svc has no ready endpoints for slot=$slot" >&2
                          kubectl -n "$ns" get endpoints "$svc" -o wide || true
                          kubectl -n "$ns" get pods -l app=aegis-warroom -o wide || true
                          exit 1
                        fi
                    '''
                }

                script {
                    env.TRAFFIC_PROMOTED = 'true'
                }
            }
        }

        stage('Smoke Test Active Service') {
            when {
                expression {
                    return params.DEPLOY_ENABLED
                }
            }

            steps {
                withCredentials([
                    file(
                        credentialsId: env.KUBECONFIG_CREDENTIALS_ID,
                        variable: 'KUBECONFIG'
                    )
                ]) {
                    sh 'bash "$K8S_ASSETS_DIR/scripts/k8s_smoke.sh"'
                }

                // External URL the judges open — must answer after promote (not only in-pod smoke).
                sh '''
                    set -eu
                    test -n "$AEGIS_API_URL" || {
                      echo "AEGIS_API_URL is empty; skipping external War Room readiness check." >&2
                      exit 0
                    }
                    echo "▶ Waiting for external War Room: $AEGIS_API_URL/api/health"
                    ok=0
                    for i in $(seq 1 30); do
                      if curl -sf --max-time 5 "$AEGIS_API_URL/api/health" | grep -qi '"status"[[:space:]]*:[[:space:]]*"ok"'; then
                        echo "▶ War Room is LIVE for judges: $AEGIS_API_URL"
                        curl -sf --max-time 5 "$AEGIS_API_URL/api/health" || true
                        ok=1
                        break
                      fi
                      echo "  attempt $i/30 — not ready yet..."
                      sleep 3
                    done
                    if [ "$ok" -ne 1 ]; then
                      echo "ERROR: War Room did not become ready at $AEGIS_API_URL/api/health" >&2
                      exit 1
                    fi
                '''
            }
        }

        stage('Publish Deploy Metadata') {
            when {
                expression {
                    return params.DEPLOY_ENABLED
                }
            }

            steps {
                echo "▶ Publishing deployment metadata to AegisPilot Correlation Agent..."

                sh '''
                    set -eu

                    . .venv/bin/activate || . .venv/Scripts/activate

                    test -n "$AEGIS_API_URL" || {
                        echo "AEGIS_API_URL must be configured in Jenkins for live deployment metadata." >&2
                        exit 1
                    }

                    python scripts/publish_deploy_metadata.py \
                        --service "$APP_NAME" \
                        --version "$IMAGE_TAG" \
                        --commit "$APP_COMMIT" \
                        --color "$DEPLOY_COLOR" \
                        --url "$AEGIS_API_URL"
                '''
            }
        }
    }

    post {
        failure {
            echo "============================================================"
            echo "✖ Pipeline failed! Preserving evidence and checking rollback"
            echo "============================================================"

            script {
                if (env.TRAFFIC_PROMOTED == 'true') {
                    withCredentials([
                        file(
                            credentialsId: env.KUBECONFIG_CREDENTIALS_ID,
                            variable: 'KUBECONFIG'
                        )
                    ]) {
                        sh 'bash "$K8S_ASSETS_DIR/scripts/k8s_rollback.sh" || true'
                    }
                } else {
                    echo 'No traffic was promoted; rollback is intentionally skipped.'
                }
            }
        }

        always {
            echo "▶ Archiving build reports and test trends..."

            junit(
                allowEmptyResults: true,
                testResults: 'reports/junit.xml'
            )

            archiveArtifacts(
                artifacts: 'reports/**',
                allowEmptyArchive: true
            )

            cleanWs(
                deleteDirs: true,
                notFailBuild: true
            )
        }
    }
}
