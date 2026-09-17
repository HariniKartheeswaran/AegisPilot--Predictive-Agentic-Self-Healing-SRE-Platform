// =============================================================================
// AegisPilot / Aegisops — Production Declarative Jenkinsfile
//
// Full CI/CD lifecycle from source commit, dependencies, tests, coverage,
// PMD static analysis, and SonarQube quality gates to immutable artifact
// delivery and deployment metadata publication for the AegisPilot Correlation Agent.
// =============================================================================

pipeline {
    agent any

    environment {
        APP_NAME         = 'aegisops'
        NAMESPACE        = 'aegispilot'
        DEPLOY_COLOR     = 'green'
        IMAGE_TAG        = "${env.GIT_COMMIT ? env.GIT_COMMIT.take(7) : 'local'}-${env.BUILD_NUMBER}"
        REGISTRY         = '123456789012.dkr.ecr.us-east-1.amazonaws.com' // Set to team registry in Jenkins
        IMAGE            = "${REGISTRY}/${APP_NAME}:${IMAGE_TAG}"
        SONAR_SERVER     = 'SonarQube-Server'
        REPORTS_DIR      = 'reports'
        // Set to your AegisPilot Cloud Run or local URL if publishing over HTTP
        AEGIS_API_URL    = "${env.AEGIS_API_URL ?: ''}"
    }

    options {
        timestamps()
        timeout(time: 30, unit: 'MINUTES')
        disableConcurrentBuilds()
    }

    stages {
        stage('Checkout') {
            steps {
                echo "▶ Checking out source commit: ${env.GIT_COMMIT ?: 'workspace'}"
                checkout scm
            }
        }

        stage('Environment / Version') {
            steps {
                echo "============================================================"
                echo "▶ Build Version  : ${IMAGE_TAG}"
                echo "▶ Target Service : ${APP_NAME}"
                echo "▶ Namespace      : ${NAMESPACE}"
                echo "▶ Target Image   : ${IMAGE}"
                echo "▶ Release Slot   : ${DEPLOY_COLOR}"
                echo "============================================================"
                sh 'mkdir -p reports'
            }
        }

        stage('Install Dependencies') {
            steps {
                echo "▶ Preparing isolated Python build environment..."
                sh '''
                    python3 -m venv .venv || python -m venv .venv
                    . .venv/bin/activate || . .venv/Scripts/activate
                    python -m pip install --upgrade pip
                    python -m pip install -r backend/requirements.txt
                    python -m pip install pytest pytest-cov flake8 httpx
                '''
            }
        }

        stage('Unit Tests') {
            steps {
                echo "▶ Running unit tests with JUnit XML reporting..."
                sh '''
                    . .venv/bin/activate || . .venv/Scripts/activate
                    python -m pytest tests/ -q --junitxml=reports/junit.xml
                '''
            }
        }

        stage('Coverage Gate') {
            steps {
                echo "▶ Enforcing minimum test coverage threshold (>= 85%)..."
                sh '''
                    . .venv/bin/activate || . .venv/Scripts/activate
                    python -m pytest tests/ --cov=backend --cov-report=xml:reports/coverage.xml --cov-report=term --cov-fail-under=85
                '''
            }
        }

        stage('Static Analysis (PMD)') {
            steps {
                echo "▶ Running static analysis & copy-paste detection..."
                sh './scripts/run_pmd.sh'
            }
        }

        stage('SonarQube Quality Gate') {
            steps {
                echo "▶ Running SonarQube Scanner analysis..."
                // Runs Sonar analysis wrapper; skips cleanly if server not configured
                sh './scripts/run_sonar.sh'
            }
        }

        // =====================================================================
        // CD & Deployment Stages (Handoff with Teammate's Docker & K8s work)
        // Once teammate's manifests and scripts are merged, these placeholders
        // will be switched to the active docker/kubectl commands.
        // =====================================================================

        stage('Docker Build (Teammate Handoff)') {
            steps {
                echo "▶ [HANDOFF] Docker Build Stage"
                echo "  Once teammate merges Dockerfile, will run: docker build -f docker/Dockerfile -t ${IMAGE} ."
            }
        }

        stage('Docker Push (Teammate Handoff)') {
            steps {
                echo "▶ [HANDOFF] Docker Push Stage"
                echo "  Once teammate configures registry credentials, will push: docker push ${IMAGE}"
            }
        }

        stage('Deploy Green (Teammate Handoff)') {
            steps {
                echo "▶ [HANDOFF] Kubernetes Green Deployment Stage"
                echo "  Will update Green slot: kubectl -n ${NAMESPACE} set image deployment/${APP_NAME}-green ${APP_NAME}=${IMAGE}"
            }
        }

        stage('Rollout Verify (Teammate Handoff)') {
            steps {
                echo "▶ [HANDOFF] Rollout Readiness Verification Stage"
                echo "  Will verify: kubectl -n ${NAMESPACE} rollout status deployment/${APP_NAME}-green --timeout=180s"
            }
        }

        stage('Smoke Test') {
            steps {
                echo "▶ Verifying service health check endpoint..."
                sh './scripts/health_check.sh'
            }
        }

        stage('Promote (Teammate Handoff)') {
            steps {
                echo "▶ [HANDOFF] Blue/Green Promotion Stage"
                echo "  Once teammate merges scripts/k8s_promote.sh, will execute: ./scripts/k8s_promote.sh"
            }
        }

        stage('Publish Deploy Metadata') {
            steps {
                echo "▶ Publishing deployment metadata to AegisPilot Correlation Agent..."
                sh '''
                    . .venv/bin/activate || . .venv/Scripts/activate
                    if [ -n "$AEGIS_API_URL" ]; then
                        python scripts/publish_deploy_metadata.py \
                            --service "$APP_NAME" \
                            --version "$IMAGE_TAG" \
                            --commit "${GIT_COMMIT:-unknown}" \
                            --color "$DEPLOY_COLOR" \
                            --url "$AEGIS_API_URL"
                    else
                        python scripts/publish_deploy_metadata.py \
                            --service "$APP_NAME" \
                            --version "$IMAGE_TAG" \
                            --commit "${GIT_COMMIT:-unknown}" \
                            --color "$DEPLOY_COLOR"
                    fi
                '''
            }
        }
    }

    post {
        failure {
            echo "============================================================"
            echo "✖ Pipeline failed! Preserving evidence and checking rollback"
            echo "============================================================"
            // Hook for automatic rollback if failure occurred during or after promotion
            sh 'if [ -f "./scripts/k8s_rollback.sh" ]; then ./scripts/k8s_rollback.sh || true; fi'
        }
        always {
            echo "▶ Archiving build reports and test trends..."
            junit allowEmptyResults: true, testResults: 'reports/junit.xml'
            archiveArtifacts artifacts: 'reports/**', allowEmptyArchive: true
            cleanWs deleteDirs: true, notFailBuild: true
        }
    }
}
