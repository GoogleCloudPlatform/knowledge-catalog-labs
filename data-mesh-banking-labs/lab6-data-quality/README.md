# Data Quality

## 1. About

Dataplex provides the following two options to validate data quality:

1. Auto data quality (Public Preview) provides an automated experience for getting quality insights about your data. Auto data quality automates and simplifies quality definition with recommendations and UI-driven workflows. It standardizes on insights with built-in reports and drives actions through alerting and troubleshooting.

2. Dataplex data quality task (Generally Available) offers a highly customizable experience to manage your own rule repository and customize execution and results, using Dataplex for managed / serverless execution. Dataplex data quality task uses an open source component, CloudDQ, that can also open up choices for customers who want to enhance the code to their needs.

High-level data quality framework architecture

![dq-arch](/data-mesh-banking-labs/lab6-data-quality/resources/imgs/dq-tech-architecture.png)

### 1.1. Prerequisites 
Successful completion of lab1, lab2 and lab4

### 1.2. Duration
~45  mins - 1 hr

### 1.3 Concepts

#### 1.3.1 YAML-based DQ specification file
A highly flexible YAML syntax to declare your data quality rules. Your CloudDQ YAML specification file needs to have the following three sections:

- Rules (defined under the top-level rules: YAML node): A list of rules to run. You can create these rules from predefined rule types, such as NOT_NULL and REGEX, or you can extend them with custom SQL statements such as CUSTOM_SQL_EXPR and CUSTOM_SQL_STATEMENT. The CUSTOM_SQL_EXPR statement flags any row that custom_sql_expr evaluated to False as a failure. The CUSTOM_SQL_STATEMENT statement flags any value returned by the whole statement as a failure.

- Row filters (defined under the top-level row_filters: YAML node): SQL expressions returning a boolean value that define filters to fetch a subset of data from the underlying entity subject for validation.

- Rule bindings (defined under the top-level rule_bindings: YAML node): Defines rules and rule filters to apply to the tables.

### 1.4. Scope of this lab

With an emphasis on using Dataplex data quality task (#2), we will perform an end-to-end data quality task for customer domain data product, in today's lab.

Lab Flow
- Reviewing the yaml file in which the dq rules are specified
- Configuring and running Dataplex's Data Quality task
- Reviewing the data quality metric published in BigQuery 
- Creating a data quality dashboard 
- Data quality incident management using Cloud logging and monitoring
- Creating data quality score tags for the data products in the catalog(again re-visit this in next register data product lab) 

### 1.5. Note
Auto Data Quality will be added in future 

### 1.6. Documentation
[About Data Quality](https://cloud.google.com/dataplex/docs/data-quality-overview)<br>

## 2. Lab
<hr>

### 2.1  Execute a Cloud Data Quality Dataplex Task

#### 2.1.1 Validate Dataplex entities 

 - Validate the entites are already discovered and registered in Dataplex 

    ![dataproduct-entities](/data-mesh-banking-labs/lab6-data-quality/resources/imgs/customer-dp-entities.png)

#### 2.1.2  Review the Yaml specification file

As part of the setup we have already defined a yaml file and stored in the gcs bucket  for assessing customer_data Data product quality. Let's review the yaml

- Open cloud shell and execute the below command to review the yaml file 

```bash
export PROJECT_ID=$(gcloud config get-value project)

gsutil cat gs://${PROJECT_ID}_dataplex_process/code/customer-source-configs/dq_customer_data_product.yaml
```

Here we have performing 3 key DQ rules: 
1. Valid customer which checks client_id is not null, not blank  and no duplicates 
2. We verify the timeliness score by checking the ingestion date is not older than 1 day
3. There is no duplicates in SSN and it's valid means it meets a certain regex pattern - " ^d{3}-?d{2}-?d{4}$" 

You can learn more about Cloud DQ [here](https://github.com/GoogleCloudPlatform/cloud-data-quality). 


#### 2.1.3  Grant the security policy
Just to make sure we have the necessary permissions, grant all the domain service accounts access to write data to central dq dataset

- Open cloud shell and execute the below command to make sure you have the access granted 

    ```
    export PROJECT_ID=$(gcloud config get-value project)

    export central_dq_policy="{\"policy\":{
    \"bindings\": [
    {
    \"role\": \"roles/dataplex.dataOwner\",
    \"members\": [
    \"serviceAccount:cc-trans-consumer-sa@${PROJECT_ID}.iam.gserviceaccount.com\",
    \"serviceAccount:cc-trans-sa@${PROJECT_ID}.iam.gserviceaccount.com\",   \"serviceAccount:customer-sa@${PROJECT_ID}.iam.gserviceaccount.com\",    \"serviceAccount:merchant-sa@${PROJECT_ID}.iam.gserviceaccount.com\"]}]}}"

    echo $central_dq_policy

    curl -X POST -H "Authorization: Bearer $(gcloud auth print-access-token)" -H "Content-Type: application.json" https://dataplex.googleapis.com/v1/projects/${PROJECT_ID}/locations/us-central1/lakes/central-operations--domain/zones/operations-data-product-zone:setIamPolicy -d "${central_dq_policy}"
    ``` 
- Go to BigQuery, check the permissions on "central_dq_results" dataset and make sure policy has been propagated

#### 2.1.4    Setup Incident Management using Cloud Logging and Monitoring 

By appending " --summary_to_stdout" flag to your data quality jobs, you can easily route the DQ summary logs to Cloud Logging. 

- Go to Cloud Logging -> Log Explorer 
- Enter the below into the query editor and click run and you will see the cloud dq output in the logs 

    ``` 
    jsonPayload.message =~ "complex_rule_validation_errors_count\": [^null]" OR jsonPayload.message =~ "failed_count\": [^0|null]" 
    ```
- Click on "Create Alert" to create an incident based on dq failures. 
        ![dq-log-alert](/data-mesh-banking-labs/lab6-data-quality/resources/imgs/dq-log-alert.png)

- Provide the below info in the "Create logs-based alert policy" screen
    - **Alert policy name**: dq-failure-alert 
    - **Documentation**: anything-you-like-to-enter
    - **Define log entries to alert on**: leave it to default dq log filtering text which is pre-populated
    - **Time between notifications** : 5 mins
    - **Incident autoclose duration**: leave it to default
    - **Who should be notified?**
    - Click on **Notification Channel** and then Click on **Manage Notification channel**: 
        - Under Email -> Click add your corp email
                ![notification-channel](/data-mesh-banking-labs/lab6-data-quality/resources/imgs/notification_channel.png)
    - Comeback to Logging screen -> Click on **Notification Channel** -> Click Refresh -> Choose the email id 
            - ![noti-email](/data-mesh-banking-labs/lab6-data-quality/resources/imgs/noti-email.png)
    - Click "Save" 
    - Sample Alert 

              ![samplealert](/data-mesh-banking-labs/lab6-data-quality/resources/imgs/alert.png)

#### 2.1.5  Execute the Data Quality task 

- Run the DQ job. No Changes Needed. 
    ```bash 
    export PROJECT_ID=$(gcloud config get-value project)

    # Google Cloud region for the Dataplex lake.
    export REGION_ID="us-central1"

    # Public Cloud Storage bucket containing the prebuilt data quality executable artifact.
    # There is one bucket for each Google Cloud region.
    export PUBLIC_GCS_BUCKET_NAME="dataplex-clouddq-artifacts-${REGION_ID}"

    # Location of DQ YAML Specifications file
    export YAML_CONFIGS_GCS_PATH="gs://${PROJECT_ID}_dataplex_process/code/customer-source-configs/dq_customer_data_product.yaml"

    # The Dataplex lake where your task is created.
    export LAKE_NAME="consumer-banking--customer--domain"

    # The BigQuery dataset where the final results of the data quality checks are stored.
    export TARGET_BQ_DATASET="central_dq_results"

    # The BigQuery table where the final results of the data quality checks are stored.
    export TARGET_BQ_TABLE="${PROJECT_ID}.central_dq_results.dq_results"

    # The unique identifier for the task.
    export TASK_ID="customer-data-product-dq"

    #DQ Service Account
    export SERVICE_ACCOUNT=customer-sa@${PROJECT_ID}.iam.gserviceaccount.com

    gcloud dataplex tasks create \
        --location="${REGION_ID}" \
        --lake="${LAKE_NAME}" \
        --trigger-type=ON_DEMAND \
        --vpc-sub-network-name="dataplex-default" \
        --execution-service-account="$SERVICE_ACCOUNT" \
        --spark-python-script-file="gs://${PUBLIC_GCS_BUCKET_NAME}/clouddq_pyspark_driver.py" \
        --spark-file-uris="gs://${PUBLIC_GCS_BUCKET_NAME}/clouddq-executable.zip","gs://${PUBLIC_GCS_BUCKET_NAME}/clouddq-executable.zip.hashsum","${YAML_CONFIGS_GCS_PATH}" \
        --execution-args=^::^TASK_ARGS="clouddq-executable.zip, ALL, ${YAML_CONFIGS_GCS_PATH}, --gcp_project_id=${PROJECT_ID}, --gcp_region_id='${REGION_ID}', --gcp_bq_dataset_id='${TARGET_BQ_DATASET}', --target_bigquery_summary_table='${TARGET_BQ_TABLE}' --summary_to_stdout " \
        "$TASK_ID"
    ```
        
#### 2.1.6  Monitor the data quality job
- Go to **Dataplex UI** -> **Process** tab -> **"Data Quality"** tab
- You will find a DQ job running with name "customer-data-product-dq". The job will take about 2-3 mins to complete. 
- Once the job is successful, proceed to the next step
- Check your email for the failure alert

#### 2.1.7  Review the Data quality metrics 
- Navigate to BigQuery->SQL Workspace and open the central_dq_results. Review the table and views created in this dataset. 
- Click on the dq_results table to preview the data quality results. Check the rows which shows the data quality metrics for the rules defined in the yaml configuration file 
    ![dq_results](/data-mesh-banking-labs/lab6-data-quality/resources/imgs/dq_results.png)
- To examine the rules that failed, run the following query. The failed record query's query can be used to learn more about the specific rows for which it failed.

    ```bash 
    SELECT rule_binding_id, rule_id,table_id,column_id,dimension, failed_records_query FROM `<your-project-id>.central_dq_results.dq_results` WHERE complex_rule_validation_success_flag is false or failed_percentage > 0.0
    ```

#### 2.1.8  Create a Data Quality Looker Studio Dashboard 
To build a sample cloudDQ dashboard in Looker Studio, please follow the steps below:

1.  Open a new normal browser window and paste the following link in the navigation bar 
    https://lookerstudio.google.com/u/0/reporting/f728e29c-a2fb-4029-a4c6-cfeee1ca32fa/page/x16FC<br>

2.  Make a copy of the dashboard by click on the details link next to the **Share** button
        ![dashboard Copy ](/data-mesh-banking-labs/lab6-data-quality/resources/imgs/dq_copy_ui.png)

3.  Setup your Looker studio account, if requested 
4.  Leave the New Data Source's defaultsettings(we will set this up later) 
        ![copy-report](/data-mesh-banking-labs/lab6-data-quality/resources/imgs/copy-report.png)
    This will make a copy of the report and have it available in edit mode
5.  Select the **Resource** menu and choose **Manage added data sources** option.
    - Click the **Edit** button under **Action** 
    - **Authorize**, if prompted 
    - Configure your new data source. Select the **edit Connection** option if you want to update the connection.
        -  Select **Project**:  ${PROJECT_ID}
        - Select **Dataset**: central_dq_results
        - Select **Table**: dq_summary
        - Click on the **Reconnect** button
        ![Reconnect UI](/dataquality-samples/dq_dashboard/resources/reconnect-ui.png)
    - Click the **Apply** button 
    - Click the **Done** button(right top corner) 
    - Click the **Close** button and then click the **View** button
    - Change the date range if needed and select drill down parameters to refresh the Dashboard. <br>
        ![dq-dashboard](/data-mesh-banking-labs/lab6-data-quality/resources/imgs/dq-dashboard.png)


#### 2.1.8  Create a Data Quality Score Tag  in the Catalog 
Once we have the DQ results available, using a custom utility which will automatically calculate the dq scores and create the data product tags. We have pre-built the utility as a java library which we can now orchestrate using Dataplex's serverless spark task 
     - Open cloud shell ad execute the below command 
        ```bash 
        export PROJECT_ID=$(gcloud config get-value project)

        gcloud dataplex tasks create customer-dp-dq-tag \
        --project=${PROJECT_ID} \
        --location=us-central1 \
        --vpc-sub-network-name=projects/${PROJECT_ID}/regions/us-central1/subnetworks/dataplex-default \
        --lake=consumer-banking--customer--domain \
        --trigger-type=ON_DEMAND \
        --execution-service-account=customer-sa@${PROJECT_ID}.iam.gserviceaccount.com \
        --spark-main-class="com.google.cloud.dataplex.templates.dataquality.DataProductQuality" \
        --spark-file-uris="gs://${PROJECT_ID}_dataplex_process/customer-source-configs/data-product-quality-tag-auto.yaml" \
        --container-image-java-jars="gs://${PROJECT_ID}_dataplex_process/code/common/tagmanager-1.0-SNAPSHOT.jar" \
        --execution-args=^::^TASK_ARGS="--tag_template_id=projects/${PROJECT_ID}/locations/us-central1/tagTemplates/data_product_quality, --project_id=${PROJECT_ID},--location=us-central1,--lake_id=consumer-banking--customer--domain,--zone_id=customer-data-product-zone,--entity_id=customer_data,--input_file=data-product-quality-tag-auto.yaml"
        ```
- Monitor the job. Go to Dataplex -> Process tab --> Custom spark --> "customer-dp-dq-tag" job. Refresh the page if you don't see your job. 
- Validate the result. Go to Dataplex -> Search under Discover ->  type "tag:data_product_quality" into the search bar  
- The customer data product should be tagged with the data quality information as show below:
    ![dq-tag-search](/data-mesh-banking-labs/lab6-data-quality/resources/imgs/dq-tag-search.png)

#### 2.1.9   Use Composer to orchestrate the Data Quality Task 

1. Go to Airflow UI 
2. Click on the “data_governane_dq_customer_data_product_wf”
3. Trigger DAG Manually and Monitor
4.  Go to Dataplex to monitor the DQ job triggerd by Composer

### 2.2  Execute Auto Data Quality 
To-be-added in future

<hr>

This concludes the lab module. Either proceed to the [main menu](../README.md) or to the [next module](../lab7-register-data-products) where you will learn to add additional business context to your data products

<hr>
