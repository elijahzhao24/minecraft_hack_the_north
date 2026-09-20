package dev.humancraft.avatar;

import io.netty.channel.embedded.EmbeddedChannel;
import dev.humancraft.mixin.ConnectionAccessor;
import net.minecraft.network.Connection;
import net.minecraft.network.PacketListener;
import net.minecraft.network.ProtocolInfo;
import net.minecraft.network.protocol.PacketFlow;

/** In-memory connection used by the server-controlled player. */
final class InternalPlayerConnection extends Connection {
	InternalPlayerConnection() {
		super(PacketFlow.SERVERBOUND);
		((ConnectionAccessor) (Object) this).humancraft$setChannel(new EmbeddedChannel());
	}

	@Override public void setReadOnly() {}
	@Override public void handleDisconnection() {}
	@Override public void setListenerForServerboundHandshake(PacketListener listener) {}
	@Override public <T extends PacketListener> void setupInboundProtocol(ProtocolInfo<T> protocol, T listener) {}
}
