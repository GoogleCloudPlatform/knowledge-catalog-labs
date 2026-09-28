
import argparse
import logging
import os
import subprocess

# Import main methods from the three scripts
import bulk_glossary_creator
import bulk_csv_files_creator
import bulk_glossary_import

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger("glossary_migration_orchestrator")

def run_script(script_name, args_list, env=None):
	"""
	Run a Python script as a subprocess and log output.
	"""
	cmd = ["python3", script_name] + args_list
	logger.info(f"Running: {' '.join(cmd)}")
	try:
		proc = subprocess.run(cmd, capture_output=True, text=True, env=env)
		logger.info(f"{script_name} returned {proc.returncode}")
		if proc.stdout:
			logger.info(f"STDOUT: {proc.stdout.strip()}")
		if proc.stderr:
			logger.warning(f"STDERR: {proc.stderr.strip()}")
		return proc.returncode == 0
	except Exception as e:
		logger.error(f"Exception running {script_name}: {e}")
		return False

def main():
	parser = argparse.ArgumentParser(description="Orchestrate bulk glossary creation, CSV generation, and import using existing scripts.")
	parser.add_argument("--project", required=True, help="GCP project id")
	parser.add_argument("--n", type=int, required=True, help="Number of glossaries to create")
	parser.add_argument("--location", default="us", help="Location (default: us)")
	parser.add_argument(
		"--terms-csv",
		required=False,
		default=os.path.join(os.path.dirname(__file__), "data", "terms.csv"),
		help="Path to terms CSV file to duplicate (default: data/terms.csv inside bg_import)"
	)
	parser.add_argument(
		"--categories-csv",
		required=False,
		default=os.path.join(os.path.dirname(__file__), "data", "categories.csv"),
		help="Path to categories CSV file to duplicate (default: data/categories.csv inside bg_import)"
	)
	parser.add_argument("--output-root", default="./datasets", help="Output root for CSVs")
	args = parser.parse_args()

	env = dict(**os.environ)

	# Step 1: Create EntryGroups & Glossaries
	logger.info("Step 1: Creating EntryGroups and Glossaries using bulk_glossary_creator.py...")
	bulk_glossary_creator.main([
		f"--project={args.project}",
		f"--location={args.location}",
		f"--count={args.n}",
		f"--output-root={args.output_root}"
	])

	# Step 2: Copy CSV files to all glossaries
	logger.info("Step 2: Copying CSV files using bulk_csv_files_creator.py...")
	bulk_csv_files_creator.main([
		f"--created-file={args.output_root}/_created_glossaries.txt",
		f"--terms-csv={args.terms_csv}",
		f"--categories-csv={args.categories_csv}",
		f"--output-root={args.output_root}"
	])

	# Step 3: Import all glossaries
	logger.info("Step 3: Importing all glossaries using bulk_glossary_import.py...")
	bulk_glossary_import.main([
		f"--output-root={args.output_root}",
		f"--project={args.project}",
		f"--location={args.location}"
	])

	logger.info("All steps completed.")

if __name__ == "__main__":
	main()
