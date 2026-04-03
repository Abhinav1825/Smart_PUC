const EmissionContract = artifacts.require("EmissionContract");

module.exports = function (deployer) {
  deployer.deploy(EmissionContract);
};
