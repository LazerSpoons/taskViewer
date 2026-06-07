import boto3
from botocore.exceptions import ClientError

REGION     = "us-east-1"
TABLE_NAME = "SystemStats"
def create_table():
    dynamodb = boto3.resource("dynamodb", region_name=REGION)
    try:
        table = dynamodb.create_table(
            TableName=TABLE_NAME,
            KeySchema=[
                {"AttributeName": "hostname",  "KeyType": "HASH"},
                {"AttributeName": "timestamp", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "hostname",  "AttributeType": "S"},
                {"AttributeName": "timestamp", "AttributeType": "S"},
            ],
            BillingMode="PROVISIONED",
            ProvisionedThroughput={
                "ReadCapacityUnits":  5,
                "WriteCapacityUnits": 5,
            },
        )
        table.meta.client.get_waiter("table_exists").wait(TableName=TABLE_NAME)
        print(f"Table '{TABLE_NAME}' created in {REGION}.")
    except ClientError as e:
        if e.response["Error"]["Code"] == "ResourceInUseException":
            print(f"Table '{TABLE_NAME}' already exists — nothing to do.")
        else:
            raise


if __name__ == "__main__":
    create_table()
