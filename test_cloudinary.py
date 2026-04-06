import cloudinary, cloudinary.uploader
cloudinary.config(cloud_name='dqk2ulxa8', api_key='999283596732226', api_secret='IhCTSPU-UzVgy4XKCaP8KSToJqY')
try:
  res = cloudinary.uploader.upload('static/uploads/chat/incident_66a27133.webm', resource_type='video')
  print('SUCCESS:', res.get('secure_url'))
except Exception as e:
  print('FAIL:', str(e))
