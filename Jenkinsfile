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
            defaultValue: 'cc18b98ffd54af59af098a4396db9f45083ee1cf',
            trim: true,
            description: 'Reviewed feature/k8s commit used as read-only deployment assets.'
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

        stage('01 - Source') {
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
            }
        }

        stage('02 - Quality Assurance') {
            stages {

                        stage('Install Dependencies') {
                            steps {
                                echo "▶ Preparing isolated Python build environment..."

                                sh '''
                                    set -eu

                                    python3 -m venv .venv || python -m venv .venv

                                    . .venv/bin/activate || . .venv/Scripts/activate

                                    python -m pip install --upgrade pip
                                    python -m pip install -r backend/requirements.txt
                                    python -m pip install pytest pytest-cov pytest-asyncio flake8 httpx ruff
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
                                echo "▶ Enforcing minimum test coverage threshold (temporary development gate: >= 60%)..."

                                sh '''
                                    set -eu

                                    . .venv/bin/activate || . .venv/Scripts/activate

                                    python -m pytest tests/ \
                                        --cov=backend \
                                        --cov-report=xml:reports/coverage.xml \
                                        --cov-report=term \
                                        --cov-fail-under=60
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
            }
        }

        stage('03 - Build & Package') {
            when {
                expression {
                    return params.DEPLOY_ENABLED
                }
            }

            stages {

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
            }
        }

        stage('04 - Kubernetes Deployment') {
            when {
                expression {
                    return params.DEPLOY_ENABLED
                }
            }

            stages {

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
            }
        }

        stage('05 - Release & Verification') {
            when {
                expression {
                    return params.DEPLOY_ENABLED
                }
            }

            stages {

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
