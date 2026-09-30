/** Task-owned private AWS resources, current pricing proof and two-hour TTL. */
import fs from 'node:fs/promises';
import path from 'node:path';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
const exec=promisify(execFile),aws=process.env.EARTHCRAFT_AWS_CLI??path.join(process.env.HOME,'.local/bin/aws');
const control=path.resolve(process.argv[2]);await fs.mkdir(control,{recursive:true});
const settings=JSON.parse(await fs.readFile(path.join(control,'aws-settings.json'),'utf8'));
const call=async(service,operation,args=[])=>JSON.parse((await exec(aws,[service,operation,...args,'--region','us-east-1','--output','json'],{maxBuffer:16*2**20})).stdout||'{}');
async function input(service,operation,value) {
 const file=path.join(control,service+'-'+operation+'.json');await fs.writeFile(file,JSON.stringify(value),{mode:0o600});
 return call(service,operation,['--cli-input-json','file://'+file]);
}
const identity=await call('sts','get-caller-identity');
if(identity.Account!==settings.account)throw new Error('AWS destination ownership changed');
const task=settings.task,bucket=task+'-'+identity.Account;
if(!/^[a-z0-9-]{8,40}$/.test(task))throw new Error('Task-scoped AWS resource name required');
const tags=[{Key:'Project',Value:'earthcraft'},{Key:'Task',Value:task},{Key:'ExpiresAfterHours',Value:'2'}];
const live=await call('ec2','describe-instances',['--filters','Name=tag:Project,Values=earthcraft','Name=instance-state-name,Values=pending,running,stopping,stopped']);
if(live.Reservations.length)throw new Error('Reuse/clean up the existing task accelerator; no duplicate launch');
const rates=await call('pricing','get-products',['--service-code','AmazonEC2','--filters',
 'Type=TERM_MATCH,Field=instanceType,Value=c7i.8xlarge','Type=TERM_MATCH,Field=location,Value=US East (N. Virginia)',
 'Type=TERM_MATCH,Field=operatingSystem,Value=Linux','Type=TERM_MATCH,Field=tenancy,Value=Shared',
 'Type=TERM_MATCH,Field=preInstalledSw,Value=NA','Type=TERM_MATCH,Field=capacitystatus,Value=Used']);
const compute=Math.max(...rates.PriceList.flatMap(raw=>Object.values(JSON.parse(raw).terms.OnDemand).flatMap(term=>
 Object.values(term.priceDimensions).filter(d=>d.unit==='Hrs').map(d=>Number(d.pricePerUnit.USD)))));
if(!(compute>0&&compute<2))throw new Error('Unexpected current accelerator price');
for(const [name,service,filters,unit,rate] of [
 ['ebs','AmazonEC2',{volumeApiName:'gp3',regionCode:'us-east-1'},'GB-Mo',0.08],
 ['ip','AmazonVPC',{usagetype:'USE1-PublicIPv4:InUseAddress',regionCode:'us-east-1'},'Hrs',0.005],
 ['s3','AmazonS3',{volumeType:'Standard',usagetype:'TimedStorage-ByteHrs',regionCode:'us-east-1'},'GB-Mo',0.023],
 ['egress','AWSDataTransfer',{transferType:'AWS Outbound',fromRegionCode:'us-east-1',toLocation:'External'},'GB',0.09],
]) {
 const response=await call('pricing','get-products',['--service-code',service,'--filters',
  ...Object.entries(filters).map(([field,value])=>'Type=TERM_MATCH,Field='+field+',Value='+value)]);
 const products=response.PriceList;
 await fs.writeFile(path.join(control,'aws-price-'+name+'.json'),JSON.stringify(products));
 const found=products.flatMap(raw=>Object.values(JSON.parse(raw).terms.OnDemand??{}).flatMap(t=>Object.values(t.priceDimensions)));
 if(!found.some(d=>d.unit===unit&&Number(d.pricePerUnit.USD)===rate))throw new Error('Current '+name+' pricing proof missing');
}
// Refresh all official price receipts before each launch. Bound egress to
// 5 MB/s and reserve $1/hour for requests/control.
const cost={account:identity.Account,compute_per_hour:compute,ebs_gib:400,ebs_per_gib_month:0.08,
 public_ip_per_hour:0.005,s3_max_gib:400,s3_per_gib_month:0.023,
 egress_max_gb_hour:18,egress_per_gb:0.09,request_allowance_per_hour:1,
 maximum_hourly:compute+400*0.08/720+0.005+400*0.023/720+18*0.09+1,
 lifetime_hours:2,pricing:rates,standing_consent_date:'2026-09-29',task};
if(cost.maximum_hourly>=10)throw new Error('Aggregate task cost exceeds prior consent');
await fs.writeFile(path.join(control,process.argv.includes('--check')?'cost-preflight.json':'cost-proof.json'),JSON.stringify(cost,null,2));
if(process.argv.includes('--check')) {
 console.log(JSON.stringify({result:'pass',account:identity.Account,maximum_hourly:cost.maximum_hourly,launch:false}));
 process.exit(0);
}
await call('s3api','create-bucket',['--bucket',bucket]);
await input('s3api','put-public-access-block',{Bucket:bucket,PublicAccessBlockConfiguration:{BlockPublicAcls:true,IgnorePublicAcls:true,BlockPublicPolicy:true,RestrictPublicBuckets:true}});
await input('s3api','put-bucket-ownership-controls',{Bucket:bucket,OwnershipControls:{Rules:[{ObjectOwnership:'BucketOwnerEnforced'}]}});
await input('s3api','put-bucket-encryption',{Bucket:bucket,ServerSideEncryptionConfiguration:{Rules:[{ApplyServerSideEncryptionByDefault:{SSEAlgorithm:'AES256'}}]}});
await input('s3api','put-bucket-tagging',{Bucket:bucket,Tagging:{TagSet:tags}});
await input('s3api','put-bucket-lifecycle-configuration',{Bucket:bucket,LifecycleConfiguration:{Rules:[{ID:'task-cleanup',Status:'Enabled',Filter:{Prefix:''},Expiration:{Days:7},AbortIncompleteMultipartUpload:{DaysAfterInitiation:1}}]}});
await input('s3api','put-bucket-policy',{Bucket:bucket,Policy:JSON.stringify({Version:'2012-10-17',Statement:[{Effect:'Deny',Principal:'*',Action:'s3:*',Resource:['arn:aws:s3:::'+bucket,'arn:aws:s3:::'+bucket+'/*'],Condition:{Bool:{'aws:SecureTransport':'false'}}}]})});
const acl=await call('s3api','get-bucket-acl',['--bucket',bucket]);
if(acl.Grants.some(g=>g.Grantee.Type!=='CanonicalUser'||g.Grantee.ID!==acl.Owner.ID))throw new Error('Bucket has non-owner access');
const block=await call('s3api','get-public-access-block',['--bucket',bucket]);
if(Object.values(block.PublicAccessBlockConfiguration).some(v=>v!==true))throw new Error('Bucket privacy not enforced');
await exec(aws,['s3','cp',path.join(control,'seed.tar.gz'),'s3://'+bucket+'/seed.tar.gz','--sse','AES256','--region','us-east-1','--only-show-errors']);
await exec(aws,['s3','cp',path.join(control,'inputs.tar.gz'),'s3://'+bucket+'/inputs.tar.gz','--sse','AES256','--region','us-east-1','--only-show-errors']);
const role=task;
await input('iam','create-role',{RoleName:role,AssumeRolePolicyDocument:JSON.stringify({Version:'2012-10-17',Statement:[{Effect:'Allow',Principal:{Service:'ec2.amazonaws.com'},Action:'sts:AssumeRole'}]}),Tags:tags});
await call('iam','attach-role-policy',['--role-name',role,'--policy-arn','arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore']);
await input('iam','put-role-policy',{RoleName:role,PolicyName:'task-bucket-only',PolicyDocument:JSON.stringify({Version:'2012-10-17',Statement:[{Effect:'Allow',Action:['s3:GetObject','s3:PutObject'],Resource:'arn:aws:s3:::'+bucket+'/*'},{Effect:'Allow',Action:['s3:ListBucket'],Resource:'arn:aws:s3:::'+bucket}]})});
await call('iam','create-instance-profile',['--instance-profile-name',role]);
await call('iam','add-role-to-instance-profile',['--instance-profile-name',role,'--role-name',role]);
const networks=await call('ec2','describe-vpcs',['--filters','Name=is-default,Values=true']);
if(networks.Vpcs.length!==1)throw new Error('Expected one configured default AWS VPC');
const vpc=networks.Vpcs[0].VpcId;
const subnets=await call('ec2','describe-subnets',['--filters','Name=vpc-id,Values='+vpc]);
const subnet=subnets.Subnets.find(s=>s.AvailabilityZone==='us-east-1a')??subnets.Subnets[0];
if(!subnet)throw new Error('No configured AWS subnet available');
const group=await input('ec2','create-security-group',{GroupName:task,Description:'Private task accelerator; no inbound rules',VpcId:vpc,TagSpecifications:[{ResourceType:'security-group',Tags:tags}]});
const image=await call('ssm','get-parameters',['--names','/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64']);
const boot=`#!/bin/bash
set -euo pipefail
/usr/sbin/shutdown -h +120
dnf install -y python3.11 python3.11-pip
command -v aws >/dev/null || dnf install -y awscli
mkdir -p /opt/earthcraft
cd /opt/earthcraft
aws s3 cp s3://${bucket}/seed.tar.gz seed.tar.gz --region us-east-1 --only-show-errors
tar -xzf seed.tar.gz
python3.11 -m venv .venv
.venv/bin/pip install numpy==2.4.6 rasterio==1.4.4 pyproj==3.7.2 shapely==2.1.2 Pillow==12.3.0 nbtlib==2.0.4 laspy==2.7.0 lazrs==0.7.0 pyshp==3.1.6 scipy==1.17.1 tifffile==2026.3.3 psutil==7.2.2 osmium==4.3.1
aws s3 cp s3://${bucket}/inputs.tar.gz inputs.tar.gz --region us-east-1 --only-show-errors
export EARTHCRAFT_CLIENT_JAR=/opt/earthcraft/vendor/minecraft-client-1.21.10.jar
export EARTHCRAFT_BULK_ROOT=/opt/earthcraft/bulk
export PYTHONPATH=/opt/earthcraft/scripts
.venv/bin/python -u scripts/regional_cloud_worker.py --root /opt/earthcraft --inputs inputs.tar.gz --bucket ${bucket} --workers 4 > worker.log 2>&1
aws s3 cp worker.log s3://${bucket}/worker.log --region us-east-1 --sse AES256 --only-show-errors
/usr/sbin/shutdown -h now
`;
await fs.writeFile(path.join(control,'user-data.sh'),boot);
// IAM propagation is bounded; the instance itself always has a terminate timer.
await new Promise(resolve=>setTimeout(resolve,12000));
const instanceOptions={ImageId:image.Parameters[0].Value,InstanceType:'c7i.8xlarge',MinCount:1,MaxCount:1,
 IamInstanceProfile:{Name:role},InstanceInitiatedShutdownBehavior:'terminate',
 MetadataOptions:{HttpTokens:'required',HttpEndpoint:'enabled'},
 NetworkInterfaces:[{DeviceIndex:0,SubnetId:subnet.SubnetId,Groups:[group.GroupId],AssociatePublicIpAddress:true}],
 BlockDeviceMappings:[{DeviceName:'/dev/xvda',Ebs:{VolumeSize:400,VolumeType:'gp3',Encrypted:true,DeleteOnTermination:true}}],
 TagSpecifications:[{ResourceType:'instance',Tags:tags},{ResourceType:'volume',Tags:tags}],UserData:Buffer.from(boot).toString('base64')};
let launched;
try{launched=await input('ec2','run-instances',instanceOptions);}
catch(error){
 if(!error.message.includes('VcpuLimitExceeded'))throw error;
 const fallback=await call('pricing','get-products',['--service-code','AmazonEC2','--filters',
  'Type=TERM_MATCH,Field=instanceType,Value=c7i.4xlarge','Type=TERM_MATCH,Field=location,Value=US East (N. Virginia)',
  'Type=TERM_MATCH,Field=operatingSystem,Value=Linux','Type=TERM_MATCH,Field=tenancy,Value=Shared',
  'Type=TERM_MATCH,Field=preInstalledSw,Value=NA','Type=TERM_MATCH,Field=capacitystatus,Value=Used']);
 const price=Math.max(...fallback.PriceList.flatMap(raw=>Object.values(JSON.parse(raw).terms.OnDemand).flatMap(t=>
  Object.values(t.priceDimensions).filter(d=>d.unit==='Hrs').map(d=>Number(d.pricePerUnit.USD)))));
 if(!(price>0&&price<compute))throw new Error('Fallback pricing proof missing');
 cost.maximum_hourly+=price-cost.compute_per_hour;cost.compute_per_hour=price;cost.pricing=fallback;
 cost.fallback_reason='Existing AWS on-demand vCPU quota';cost.instance_type='c7i.4xlarge';
 await fs.writeFile(path.join(control,'cost-proof.json'),JSON.stringify(cost,null,2));
 instanceOptions.InstanceType='c7i.4xlarge';launched=await input('ec2','run-instances',instanceOptions);
}
const record={bucket,role,group:group.GroupId,instance:launched.Instances[0].InstanceId,account:identity.Account,
 deadline:Date.now()/1000+7200,maximum_hourly:cost.maximum_hourly,privacy_verified:true};
await fs.writeFile(path.join(control,'resources.json'),JSON.stringify(record,null,2));
console.log(JSON.stringify(record));
