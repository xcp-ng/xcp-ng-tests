def setup(Map args = [:])
{
    env.debug_prefix="" // Takeover a cmdline in a shell (if defined)
    env.debug_suffix="" // Additonal args to cmdline
    // Opt-in: clean abort of pytest, with monitor() in post
    if (args.abort_monitor && fileExists('scripts/jenkins_pytest.py')) {
        env.JENKINS_PYTEST_ABORT = '1'
        if (params.DEBUG) {
            env.debug_prefix = "scripts/jenkins_pytest.py run debug-${env.BUILD_NUMBER}"
            env.debug_suffix = "--pdb"
        }
        return
    }
    if (params.DEBUG) {
        env.PATH = sh(script: '''
file=$(ls -d "${JENKINS_HOME}/workspace/${JOB_NAME}@script/"*/bin/detash.sh | head -n1)
dirname "${file}"
''' , returnStdout: true).trim() + ":" + env.PATH
        echo "# PATH=${env.PATH}"
        env.debug_prefix="detash.sh" // To takeover session from shell
        env.DETASH_NAME="${env.BUILD_TAG}"
        env.debug_suffix="--pdb" // Start debugger on failure
        env.DETASH_ON_QUIT="continue" // Continue to teardown pytest before exit

        // sh(script: 'detash.sh --help') // keep commented when not testing
        // env.debug_prefix="${env.debug_prefix} top #"// TODO: cmdline will be ignored
        // env.DETASH_ON_QUIT="h" // TODO: Check it displays stuff in screenlog

        echo "WARNING: Run in detashed mode, on stuck job take it over using:"
        echo "# ssh ${env.NODE_NAME} screen -r ${env.DETASH_NAME}"
        echo "# Then from (Pdb) shell type 'continue' to tear pytest down"
    }
}

def monitor()
{
    timeout(time: 15, unit: 'MINUTES') { // Acts as a second abort
        sh '[ ! -x scripts/jenkins_pytest.py ] || scripts/jenkins_pytest.py monitor'
    }
}
return this
