import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15

Rectangle {
    id: root
    objectName: "statusStrip"

    required property var sessionState
    required property var displayPreferences
    required property string applicationVersion
    property bool narrow: false

    color: Theme.surfaceLow
    implicitHeight: 34

    RowLayout {
        anchors.fill: parent
        anchors.leftMargin: 16
        anchors.rightMargin: 16
        spacing: 12

        Rectangle {
            Layout.preferredWidth: 8
            Layout.preferredHeight: 8
            radius: 4
            color: root.sessionState.errors && root.sessionState.errors.length > 0
                ? Theme.error
                : root.sessionState.progress ? Theme.warning : Theme.primary
        }

        Label {
            Layout.fillWidth: true
            text: root.sessionState.status ? root.sessionState.status.message : "Starting"
            color: Theme.textMuted
            font.pixelSize: Theme.textPixelSize(11)
            elide: Text.ElideRight
        }

        Label {
            objectName: "statusPersistenceMessage"
            Layout.fillWidth: true
            Layout.minimumWidth: 0
            text: root.displayPreferences.persistenceMessage
            color: root.displayPreferences.persistenceMessage === "Saved"
                ? Theme.primary
                : Theme.warning
            font.pixelSize: Theme.textPixelSize(11)
            elide: Text.ElideRight
            Accessible.name: text
            Accessible.description: text
        }

        Label {
            objectName: "statusProfileMessage"
            Layout.fillWidth: true
            text: {
                const profile = root.sessionState.set_profile
                if (!profile || !profile.maturity)
                    return "Profile unavailable"
                const outcome = profile.refresh_outcome
                return "Profile · " + profile.maturity
                    + (outcome ? " · " + outcome : "")
            }
            color: Theme.textMuted
            font.pixelSize: Theme.textPixelSize(11)
            elide: Text.ElideRight
            Accessible.name: text
            Accessible.description: "Current set-profile maturity and refresh outcome."
        }

        Label {
            text: root.sessionState.ratings ? root.sessionState.ratings.message : "Ratings unavailable"
            color: Theme.textMuted
            font.pixelSize: Theme.textPixelSize(11)
            elide: Text.ElideRight
        }

        Label {
            objectName: "applicationVersionLabel"
            text: "v" + root.applicationVersion
            color: Theme.textMuted
            font.pixelSize: Theme.textPixelSize(11)
            Accessible.name: "Draft Omen version " + root.applicationVersion
        }

        Label {
            visible: !root.narrow
            text: "Data from 17Lands"
            color: Theme.primary
            font.pixelSize: Theme.textPixelSize(11)
        }
    }

    Rectangle {
        anchors.top: parent.top
        width: parent.width
        height: 1
        color: Theme.outline
    }
}

