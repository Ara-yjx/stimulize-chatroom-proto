aws bedrock-runtime invoke-model \
  --region us-east-2 \
  --model-id arn:aws:bedrock:us-east-2:330798719009:inference-profile/global.anthropic.claude-sonnet-4-6 \
  --content-type application/json \
  --accept application/json \
  --body '{
    "anthropic_version":"bedrock-2023-05-31",
    "max_tokens":1024,
    "messages":[
      {
        "role":"user",
        "content":[
          {
            "type":"text",
            "text":"Hello world"
          }
        ]
      }
    ]
  }' \
  --cli-binary-format raw-in-base64-out \
  response.json