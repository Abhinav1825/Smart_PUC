const EmissionContract = artifacts.require("EmissionContract");
const PUCCertificate = artifacts.require("PUCCertificate");

module.exports = function (deployer) {
  deployer.deploy(EmissionContract);
  deployer.deploy(PUCCertificate);
};
