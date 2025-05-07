const { exec } = require('child_process');

console.log('Démarrage de l\'application Flask...');

// Lancer le script Python run.py
const pythonProcess = exec('python run.py', (error, stdout, stderr) => {
  if (error) {
    console.error(`Erreur lors du démarrage de l'application Flask: ${error}`);
    return;
  }
  console.log(`Sortie: ${stdout}`);
  if (stderr) {
    console.error(`Erreurs: ${stderr}`);
  }
});

// Rediriger la sortie du processus Python vers la console
pythonProcess.stdout.on('data', (data) => {
  console.log(`${data}`);
});

pythonProcess.stderr.on('data', (data) => {
  console.error(`${data}`);
});

pythonProcess.on('close', (code) => {
  console.log(`Le processus Python s'est arrêté avec le code: ${code}`);
});

// Gérer l'arrêt propre du processus
process.on('SIGINT', () => {
  console.log('Arrêt de l\'application Flask...');
  pythonProcess.kill();
  process.exit();
});