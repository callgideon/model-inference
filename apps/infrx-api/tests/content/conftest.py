def pytest_configure(config):
    config.addinivalue_line("markers", "s3: runs on the lane's task-local MinIO (INFRX_C2_S3=1)")
    config.addinivalue_line("markers", "pg: runs on lab-sql's C2-RPC (INFRX_D_TASK=lab-c2)")
