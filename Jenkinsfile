pipeline {
    agent any

    environment {
        PATH = "${PATH}:${HOME}/.local/bin"
    }

    stages {
        stage('Cloning the repo') {
            steps {
                git branch: 'f190385', url: 'https://github.com/SameedZ/researchgpt.git'
                sh 'pip install poetry'

            }
        }

        stage('Lint with flake8') {
            steps {
                sh 'pip install flake8'
                sh 'flake8 . --count --select=E9,F63,F7,F82 --show-source -
